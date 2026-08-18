#!/usr/bin/env python3
"""Phase 7 — advisory model discovery. NEVER part of the review execution path.

Two sources, both fail-soft:

- Primary: the installed CLI's own ``~/.codex/models_cache.json`` — the only
  source that knows which models THIS CLI supports (slugs plus
  ``supported_reasoning_levels``). It is an undocumented internal artifact
  whose schema churns (codex 0.146.0's own reader currently fails on it),
  so every read here degrades to ``None`` on any problem.
- Pricing annotation (``--refresh`` only): the public OpenAI models page.
  API per-token prices are DISPLAY-ONLY — they do not model subscription
  quota consumption. Fetched webpage content is untrusted data: the parser
  extracts structured ids/prices and never interprets anything as
  instructions.

``run_codex_review`` does NOT import this module (test-enforced): catalog
availability can degrade ``list-models`` output only; the paid ``doctor``
remains the sole validator of an execution profile.

Cache files live under ``$XDG_CACHE_HOME/codex-reviewed-implementation/``,
never inside a reviewed worktree (an in-worktree cache would dirty the
target fingerprint and break the doctor's read-only check).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CATALOG_URL = "https://developers.openai.com/api/docs/models"
CACHE_DIR_NAME = "codex-reviewed-implementation"
CACHE_FILE_NAME = "model-catalog.json"
FETCH_TIMEOUT_SECONDS = 10.0
STALE_AFTER_DAYS = 14

PRICING_NOTE = (
    "API pricing only; may not reflect subscription quota consumption."
)

# Fetched HTML is untrusted data: extract only these structured shapes.
_PRICE_RE = re.compile(
    r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*Input MTok.*?"
    r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*Output MTok",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_RE = re.compile(r"<h[23][^>]*>\s*([a-z0-9][a-z0-9._-]{2,})\s*</h[23]>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Native ~/.codex/models_cache.json — fail-soft primary source
# ---------------------------------------------------------------------------

def _load_native_cache(
    codex_home: str | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Read+parse the CLI's own model cache ONCE; ``(None, None)`` on any
    problem (fail-soft — including type-valid but non-object JSON, which the
    CLI's own reader currently trips over in the wild)."""
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, None
        models = data.get("models")
        if not isinstance(models, list):
            return None, None
        out: list[dict] = []
        for entry in models:
            if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
                continue
            levels = entry.get("supported_reasoning_levels")
            efforts = [
                lv.get("effort") for lv in levels
                if isinstance(lv, dict) and isinstance(lv.get("effort"), str)
            ] if isinstance(levels, list) else []
            out.append({
                "slug": entry["slug"],
                "display_name": entry.get("display_name"),
                "default_reasoning_level": entry.get("default_reasoning_level"),
                "supported_efforts": efforts,
            })
        stamp = data.get("fetched_at")
        return out, stamp if isinstance(stamp, str) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None, None


def read_native_models_cache(codex_home: str | None = None) -> list[dict] | None:
    """Read the CLI's own model cache; ``None`` on ANY problem (fail-soft)."""
    return _load_native_cache(codex_home)[0]


def native_cache_fetched_at(codex_home: str | None = None) -> str | None:
    """Best-effort ``fetched_at`` of the CLI's own model cache (fail-soft)."""
    return _load_native_cache(codex_home)[1]


# ---------------------------------------------------------------------------
# Cache placement + persistence (outside reviewed worktrees)
# ---------------------------------------------------------------------------

def cache_path(cache_base: str | None = None) -> Path:
    if cache_base:
        base = Path(cache_base)
    elif os.environ.get("XDG_CACHE_HOME"):
        base = Path(os.environ["XDG_CACHE_HOME"])
    else:
        base = Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache")
    return base / CACHE_DIR_NAME / CACHE_FILE_NAME


def ensure_cache_path_outside_worktree(cache_file: str, worktree: str) -> None:
    """Reject a cache path that would land inside a reviewed worktree.

    A cache write inside the worktree dirties the target fingerprint and
    breaks the doctor's read-only before/after comparison.
    """
    cf = os.path.realpath(os.path.abspath(cache_file))
    wt = os.path.realpath(os.path.abspath(worktree)).rstrip(os.sep)
    if cf == wt or cf.startswith(wt + os.sep):
        raise ValueError(
            f"catalog cache path {cache_file!r} is inside the reviewed "
            f"worktree {worktree!r}; move it outside (default: "
            f"$XDG_CACHE_HOME/{CACHE_DIR_NAME}/)"
        )


def load_cached_catalog(cache_base: str | None = None) -> dict | None:
    try:
        data = json.loads(cache_path(cache_base).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def save_cached_catalog(cache_base: str | None, data: dict[str, Any]) -> None:
    """Persist the advisory cache. Never raises: an unwritable cache
    directory degrades silently — the catalog is display-only."""
    try:
        path = cache_path(cache_base)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".catalog-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        pass  # advisory persistence: cache-dir failure is never fatal


# ---------------------------------------------------------------------------
# Webpage pricing annotation (untrusted data → structured numbers only)
# ---------------------------------------------------------------------------

MAX_CATALOG_BYTES = 4 * 1024 * 1024  # bound memory + regex work on a hostile page


def _default_fetch(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return resp.read(MAX_CATALOG_BYTES).decode("utf-8", errors="replace")


def _parse_page_prices(html: str) -> dict[str, tuple[float, float]]:
    """Best-effort structured extraction. Never raises; junk → empty."""
    prices: dict[str, tuple[float, float]] = {}
    try:
        headings = list(_HEADING_RE.finditer(html))
        for i, match in enumerate(headings):
            slug = match.group(1).lower()
            region_end = headings[i + 1].start() if i + 1 < len(headings) else len(html)
            found = _PRICE_RE.search(html[match.end():region_end])
            if found:
                prices[slug] = (float(found.group(1)), float(found.group(2)))
    except Exception:
        return {}
    return prices


# ---------------------------------------------------------------------------
# list_models — the advisory nil-matrix (never raises into a caller)
# ---------------------------------------------------------------------------

def _is_stale(fetched_at: str | None) -> bool:
    if not fetched_at:
        return True
    try:
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age > STALE_AFTER_DAYS * 86400


def _validated_cached_models(cached: dict | None) -> list[dict] | None:
    """Shape-validate cached model entries before serving them; ``None`` when
    the cache is absent or its models value is not a list of slug-bearing
    dicts (a tampered/corrupt cache must degrade, never crash consumers)."""
    if not cached:
        return None
    models = cached.get("models")
    if not isinstance(models, list):
        return None
    ok = [m for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str)]
    return ok or None


def list_models(
    *,
    refresh: bool = False,
    codex_home: str | None = None,
    cache_base: str | None = None,
    fetch: Callable[[str, float], str] | None = None,
) -> dict[str, Any]:
    """Best-effort model listing for humans and ``--json`` consumers.

    Returns ``{"models": [...], "source": native|web|cached|none, "stale":
    bool, "warnings": [...], "pricing_note": str}``. Advisory by contract:
    every failure degrades the display (a warning is added) and NEVER raises
    — catalog state can never block a doctor-valid model.
    """
    warnings: list[str] = []
    fetch = fetch or _default_fetch

    # One read+parse of the CLI's cache serves both the models and the stamp.
    native, native_fetched_at = _load_native_cache(codex_home)
    if native is None:
        warnings.append(
            "native models cache unavailable (unreadable, corrupt, or absent); "
            "compatibility info omitted"
        )

    page_prices: dict[str, tuple[float, float]] = {}
    if refresh:
        try:
            page_prices = _parse_page_prices(fetch(CATALOG_URL, FETCH_TIMEOUT_SECONDS))
            if not page_prices:
                warnings.append("catalog page parsed but no pricing found")
        except Exception as exc:  # network, timeout, parse — all advisory
            warnings.append(f"catalog refresh failed ({exc}); serving without update")

    models: list[dict[str, Any]] = [dict(m) for m in (native or [])]
    if page_prices:
        for m in models:
            price = page_prices.get(m["slug"])
            if price:
                m["input_price_per_mtok"] = price[0]
                m["output_price_per_mtok"] = price[1]
        # Annotate page-only models too (native cache may lag the catalog).
        known = {m["slug"] for m in models}
        for slug, price in page_prices.items():
            if slug not in known:
                models.append({
                    "slug": slug,
                    "supported_efforts": [],
                    "input_price_per_mtok": price[0],
                    "output_price_per_mtok": price[1],
                })

    if native:
        source = "native"
        fetched_at = native_fetched_at
    else:
        # Page-only output is honest about its origin: these slugs came from
        # the webpage, not the CLI's compatibility-bounded cache.
        source = "web" if page_prices else "none"
        fetched_at = None
    if models:
        if refresh and page_prices:
            fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            save_cached_catalog(cache_base, {
                "fetched_at": fetched_at, "catalog_url": CATALOG_URL, "models": models,
            })
        elif not native:
            cached = load_cached_catalog(cache_base)
            cached_models = _validated_cached_models(cached)
            if cached_models:
                models = cached_models
                fetched_at = cached.get("fetched_at") if isinstance(cached, dict) else None
                source = "cached"
    if source == "none" and not models:
        cached = load_cached_catalog(cache_base)
        cached_models = _validated_cached_models(cached)
        if cached_models:
            models = cached_models
            fetched_at = cached.get("fetched_at") if isinstance(cached, dict) else None
            source = "cached"

    return {
        "models": models,
        "source": source,
        "stale": _is_stale(fetched_at),
        "warnings": warnings,
        "pricing_note": PRICING_NOTE if any(
            "input_price_per_mtok" in m for m in models
        ) else "",
    }
