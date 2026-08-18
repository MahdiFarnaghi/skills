#!/usr/bin/env python3
"""Tests for codex_model_catalog.py — Phase 7 advisory model discovery.

Hermetic: no network (fetch is injected), no real ~/.codex. The catalog is
ADVISORY ONLY — every failure mode must degrade the display, never raise
into a review path.
Run: `pytest scripts/test_codex_model_catalog.py` from the skill root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_model_catalog as cat  # noqa: E402


NATIVE_FIXTURE = {
    "client_version": "0.146.0",
    "etag": "abc",
    "fetched_at": "2026-08-18T12:00:00Z",
    "models": [
        {
            "slug": "gpt-5.6-luna",
            "display_name": "GPT-5.6-Luna",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [
                {"effort": "low"}, {"effort": "medium"}, {"effort": "high"},
            ],
        },
        {
            "slug": "gpt-5.6-terra",
            "display_name": "GPT-5.6-Terra",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "high"}],
        },
    ],
}


# ---------------------------------------------------------------------------
# Native ~/.codex/models_cache.json — fail-soft primary source
# ---------------------------------------------------------------------------

def test_read_native_cache_parses_slugs_and_efforts(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(NATIVE_FIXTURE), encoding="utf-8")
    models = cat.read_native_models_cache(str(home))
    assert models is not None
    slugs = {m["slug"] for m in models}
    assert slugs == {"gpt-5.6-luna", "gpt-5.6-terra"}
    luna = next(m for m in models if m["slug"] == "gpt-5.6-luna")
    assert luna["supported_efforts"] == ["low", "medium", "high"]


def test_read_native_cache_corrupt_returns_none(tmp_path):
    # LIVE EVIDENCE: codex 0.146.0's own reader currently fails on its cache
    # ("missing field base_instructions") — our reader must be fail-soft.
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text("{not json", encoding="utf-8")
    assert cat.read_native_models_cache(str(home)) is None


def test_read_native_cache_non_object_json_fails_soft(tmp_path):
    # Type-valid but non-object JSON must not crash the never-raises contract
    # (an AttributeError here would take down list-models/validate-config).
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert cat.read_native_models_cache(str(home)) is None
    assert cat.native_cache_fetched_at(str(home)) is None
    out = cat.list_models(codex_home=str(home), cache_base=str(tmp_path / "c"))
    assert out["source"] == "none"
    assert out["warnings"]


def test_cached_catalog_bad_models_shape_degrades(tmp_path):
    # A tampered/corrupt cache with a non-list models value must never reach
    # consumers (m["slug"] would crash); it degrades to source "none".
    home = tmp_path / "codexhome"; home.mkdir()  # no native cache
    cache_base = tmp_path / "c"
    cat.cache_path(str(cache_base)).parent.mkdir(parents=True, exist_ok=True)
    cat.cache_path(str(cache_base)).write_text(
        json.dumps({"fetched_at": "2026-08-01T00:00:00Z", "models": "boom"}),
        encoding="utf-8",
    )
    out = cat.list_models(codex_home=str(home), cache_base=str(cache_base))
    assert out["models"] == []


def test_list_models_serves_cached_pricing_with_native_models(tmp_path):
    # After a --refresh persists pricing, a plain list-models with native
    # data present must still show the pricing (the offline cache must not
    # lose its annotations the moment the native cache exists).
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(NATIVE_FIXTURE), encoding="utf-8")
    webpage = ("<html><body><h2>gpt-5.6-luna</h2>"
               "<p>$1.25 / Input MTok &middot; $10 / Output MTok</p></body></html>")
    cat.list_models(
        refresh=True, codex_home=str(home), cache_base=str(tmp_path / "c"),
        fetch=lambda url, timeout: webpage,
    )
    # plain call, no refresh: native models + cached pricing
    out = cat.list_models(codex_home=str(home), cache_base=str(tmp_path / "c"))
    assert out["source"] == "native"
    luna = next(m for m in out["models"] if m["slug"] == "gpt-5.6-luna")
    assert luna.get("input_price_per_mtok") == 1.25
    assert luna.get("output_price_per_mtok") == 10.0


def test_list_models_page_only_refresh_labels_source_web(tmp_path):
    # Native cache absent + page parsed: the output is honest about coming
    # from the webpage (no CLI compatibility info), labeled "web".
    home = tmp_path / "codexhome"; home.mkdir()
    webpage = ("<html><body><h2>gpt-5.6-page-only</h2>"
               "<p>$1 / Input MTok &middot; $2 / Output MTok</p></body></html>")
    out = cat.list_models(
        refresh=True, codex_home=str(home), cache_base=str(tmp_path / "c"),
        fetch=lambda url, timeout: webpage,
    )
    assert out["source"] == "web"
    assert any(m["slug"] == "gpt-5.6-page-only" for m in out["models"])


def test_read_native_cache_missing_returns_none(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    assert cat.read_native_models_cache(str(home)) is None


# ---------------------------------------------------------------------------
# Cache placement — never inside a reviewed worktree
# ---------------------------------------------------------------------------

def test_cache_path_defaults_to_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = cat.cache_path()
    assert str(tmp_path) in str(p)
    assert "codex-reviewed-implementation" in str(p)


def test_cache_path_rejected_inside_worktree(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    bad = str(wt / "catalog-cache.json")
    try:
        cat.ensure_cache_path_outside_worktree(bad, str(wt))
    except ValueError as exc:
        assert "worktree" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_cache_path_outside_worktree_accepted(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    ok = str(tmp_path / "elsewhere" / "catalog.json")
    cat.ensure_cache_path_outside_worktree(ok, str(wt))  # no raise


# ---------------------------------------------------------------------------
# list_models — the advisory nil-matrix (never raises)
# ---------------------------------------------------------------------------

def test_list_models_from_native_source(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(NATIVE_FIXTURE), encoding="utf-8")
    out = cat.list_models(codex_home=str(home), cache_base=str(tmp_path / "c"))
    assert out["source"] == "native"
    assert {m["slug"] for m in out["models"]} >= {"gpt-5.6-luna", "gpt-5.6-terra"}


def test_list_models_falls_back_to_cache_when_native_absent(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()  # no native cache
    cache_base = tmp_path / "c"; cache_base.mkdir()
    cat.save_cached_catalog(cache_base, {"fetched_at": "2026-08-01T00:00:00Z",
                                         "models": [{"slug": "gpt-5.4"}]})
    out = cat.list_models(codex_home=str(home), cache_base=str(cache_base))
    assert out["source"] == "cached"
    assert out["stale"] is True  # old fetched_at


def test_list_models_with_nothing_available_degrades_not_raises(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    out = cat.list_models(codex_home=str(home), cache_base=str(tmp_path / "c"))
    assert out["source"] == "none"
    assert out["models"] == []
    assert out["warnings"]  # operator sees why


def test_list_models_refresh_parses_webpage_pricing(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(NATIVE_FIXTURE), encoding="utf-8")
    webpage = (
        "<html><body>"
        "<h2>gpt-5.6-luna</h2><p>$1.25 / Input MTok &middot; $10 / Output MTok</p>"
        "<h2>gpt-5.6-terra</h2><p>$8 / Input MTok &middot; $64 / Output MTok</p>"
        "</body></html>"
    )
    out = cat.list_models(
        refresh=True, codex_home=str(home), cache_base=str(tmp_path / "c"),
        fetch=lambda url, timeout: webpage,
    )
    luna = next(m for m in out["models"] if m["slug"] == "gpt-5.6-luna")
    assert luna.get("input_price_per_mtok") == 1.25
    assert luna.get("output_price_per_mtok") == 10.0
    # API prices are annotated, never presented as quota truth
    assert out["pricing_note"]
    # the refreshed result was persisted for offline reuse
    assert cat.load_cached_catalog(tmp_path / "c") is not None


def test_list_models_refresh_failure_keeps_serving(tmp_path):
    home = tmp_path / "codexhome"; home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(NATIVE_FIXTURE), encoding="utf-8")

    def broken_fetch(url, timeout):
        raise OSError("offline")

    out = cat.list_models(
        refresh=True, codex_home=str(home), cache_base=str(tmp_path / "c"),
        fetch=broken_fetch,
    )
    assert out["source"] == "native"  # refresh failed; native still served
    assert any("offline" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Import isolation — the catalog must never sit in the review execution path
# ---------------------------------------------------------------------------

def test_run_codex_review_does_not_import_catalog():
    scripts = os.path.dirname(os.path.abspath(__file__))
    code = (
        "import sys; sys.path.insert(0, %r); import run_codex_review; "
        "assert 'codex_model_catalog' not in sys.modules, 'catalog leaked into "
        "the transport'; print('ISOLATED')" % scripts
    )
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert "ISOLATED" in cp.stdout


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"]).returncode)
