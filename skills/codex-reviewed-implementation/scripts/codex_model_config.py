#!/usr/bin/env python3
"""Phase 7 — execution-profile resolution and recorded model policy.

Public API (the tested seam):

- ``load_config(path)`` — strict closed-schema TOML read of a project
  ``.codex-review.toml`` (unknown keys are rejected so a typo can never
  silently demote a project to defaults).
- ``ExecutionProfile`` — the canonical, immutable record of what a review
  will run: model, reasoning effort, native codex profile, config version,
  and where the values were resolved from. One digest over the
  execution-affecting fields is the binding key used by doctor receipts,
  review receipts, and the loop-state ledger.
- ``resolve_profile(...)`` — the precedence chain:
  CLI flags > project config > recorded native default (warn), with a hard
  ``E_CONFIG_MISSING`` failure only when ``require_config`` is set (CI).
- ``write_config(path, ...)`` — closed-schema TOML emitter (atomic,
  parse-back validated, refuses overwrite without ``force``).

This module is deliberately dependency-free (stdlib ``tomllib`` for reads;
a tiny emitter for the closed schema — never a general TOML serializer).
Catalog data is advisory and lives in ``codex_model_catalog``; nothing here
may block a doctor-valid model on catalog state.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_FILENAME = ".codex-review.toml"
SUPPORTED_CONFIG_VERSION = 1

#: Sentinel model value that restores pre-Phase-7 behavior: pass no ``-m``
#: and let the Codex CLI choose. Normalizes to ``model=None`` in the profile.
CLI_DEFAULT = "cli-default"

VALID_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")

#: Closed schema: the only keys a project config may carry.
CONFIG_KEYS = ("version", "model", "reasoning_effort", "codex_profile")

_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Errors — stable codes with verbatim remediation templates (plan T3b)
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Configuration failure carrying a stable code and actionable fix."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def config_missing_message(worktree: str) -> str:
    return (
        "E_CONFIG_MISSING: No review model is configured for "
        f"{worktree} and stdin is non-interactive.\n"
        "Fix: run `run_codex_review.py init-config --worktree <path> "
        "--model <MODEL>` or pass `--model <MODEL>` (or `--model cli-default` "
        "to keep the Codex CLI's own default)."
    )


# ---------------------------------------------------------------------------
# Strict closed-schema TOML reading
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict[str, Any]:
    """Load and validate a project ``.codex-review.toml``.

    Raises ``ConfigError`` (E_CONFIG_MALFORMED) with the offending key named
    on: unreadable file, invalid TOML, non-object root, unknown key, wrong
    version, or a value that fails its type/shape check. Malformed config is
    a loud error — never a silent fall-through to defaults (invariant 3).
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: cannot read config {path}: {exc}",
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: config {path} is not valid TOML: {exc}. "
            "Fix: correct the syntax, then run "
            "`run_codex_review.py validate-config --worktree <path>`.",
        ) from exc

    unknown = [k for k in data if k not in CONFIG_KEYS]
    if unknown:
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: config {path} has unknown key(s) "
            f"{', '.join(sorted(unknown))}; allowed keys: {', '.join(CONFIG_KEYS)}. "
            "Fix: remove or correct the key (check for "
            "reasoning-effort vs reasoning_effort).",
        )

    version = data.get("version")
    if version != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            "E_CONFIG_UNSUPPORTED_VERSION",
            f"E_CONFIG_MALFORMED: config {path} version must be "
            f"{SUPPORTED_CONFIG_VERSION}, got {version!r}.",
        )

    model = data.get("model")
    if model is not None:
        if not isinstance(model, str) or not _MODEL_ID_RE.match(model):
            raise ConfigError(
                "E_CONFIG_MALFORMED",
                f"E_CONFIG_MALFORMED: config {path} key 'model' must be a "
                f"model id string or \"{CLI_DEFAULT}\", got {model!r}.",
            )

    effort = data.get("reasoning_effort")
    if effort is not None:
        if not isinstance(effort, str) or effort not in VALID_REASONING_EFFORTS:
            raise ConfigError(
                "E_CONFIG_MALFORMED",
                f"E_CONFIG_MALFORMED: config {path} key 'reasoning_effort' must "
                f"be one of {VALID_REASONING_EFFORTS}, got {effort!r}.",
            )

    codex_profile = data.get("codex_profile")
    if codex_profile is not None and not isinstance(codex_profile, str):
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: config {path} key 'codex_profile' must be a "
            f"string, got {codex_profile!r}.",
        )

    return data


# ---------------------------------------------------------------------------
# ExecutionProfile — one canonical identity bound into every artifact
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionProfile:
    """The canonical record of what a Codex invocation will run.

    ``digest`` hashes only the execution-affecting fields (model,
    reasoning_effort, codex_profile, config_version) — provenance
    (``resolution_source``) and advisory metadata never change authorization.
    ``model`` is ``None`` when no ``-m`` is passed (the CLI chooses; also the
    normalized form of the ``cli-default`` sentinel).
    """

    model: str | None
    reasoning_effort: str | None
    codex_profile: str | None
    config_version: int | str  # file version, or the "none" sentinel
    resolution_source: str  # cli_flag | project_config | user_config | cli_default

    def __post_init__(self) -> None:
        # Normalize the cli-default sentinel: identical execution → identical
        # identity. Intent is preserved via resolution_source.
        if self.model == CLI_DEFAULT:
            object.__setattr__(self, "model", None)

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "codex_profile": self.codex_profile,
                "config_version": self.config_version,
            },
            sort_keys=True,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def receipt_fields(self) -> dict[str, Any]:
        """The profile fields bound into doctor receipts, review receipts,
        ledger claims, and quota observations."""
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "codex_profile": self.codex_profile,
            "config_version": self.config_version,
            "resolution_source": self.resolution_source,
            "profile_digest": self.digest,
        }


# ---------------------------------------------------------------------------
# Resolution — the precedence chain
# ---------------------------------------------------------------------------

def _read_user_model_pin(codex_home: str | None) -> str | None:
    """Fail-soft read of the user's ``config.toml`` model pin.

    Records provenance only — the wrapper never adopts the pin itself (no
    ``-m`` is passed; the CLI applies its own config). Unreadable/corrupt
    user config is a warning-level condition, never an error.
    """
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        data = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    pin = data.get("model")
    return pin if isinstance(pin, str) and pin else None


def resolve_profile(
    *,
    cli_model: str | None = None,
    cli_effort: str | None = None,
    cli_codex_profile: str | None = None,
    worktree: str | None = None,
    require_config: bool = False,
    codex_home: str | None = None,
) -> ExecutionProfile:
    """Resolve the execution profile: CLI flags > project config > recorded
    native default.

    - Any CLI flag present → ``cli_flag``; unset fields inherit the project
      config when one exists.
    - No flags + a project config carrying a model/effort/profile →
      ``project_config``.
    - No flags + no (or empty) project config → the native default is used
      and its provenance recorded (``user_config`` when the user's config
      pins a model, else ``cli_default``). A warning belongs at the call
      site; resolution itself succeeds so zero-config projects keep working.
    - ``require_config`` (CI) turns the last case into a loud
      ``E_CONFIG_MISSING`` instead.
    - A malformed project config is always a loud ``ConfigError`` — never a
      silent fall-through (invariant 3).
    """
    if cli_effort is not None and cli_effort not in VALID_REASONING_EFFORTS:
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: --reasoning-effort must be one of "
            f"{VALID_REASONING_EFFORTS}, got {cli_effort!r}.",
        )
    if cli_model is not None and cli_model != CLI_DEFAULT \
            and not _MODEL_ID_RE.match(cli_model):
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: --model must be a model id or '{CLI_DEFAULT}', "
            f"got {cli_model!r}.",
        )

    config: dict[str, Any] = {}
    config_version: int | str = "none"
    if worktree:
        path = os.path.join(worktree, CONFIG_FILENAME)
        if os.path.isfile(path):
            config = load_config(path)  # loud on malformed
            config_version = config.get("version", "none")

    has_cli = any(v is not None for v in (cli_model, cli_effort, cli_codex_profile))
    if has_cli:
        return ExecutionProfile(
            model=cli_model if cli_model is not None else config.get("model"),
            reasoning_effort=cli_effort if cli_effort is not None
            else config.get("reasoning_effort"),
            codex_profile=cli_codex_profile if cli_codex_profile is not None
            else config.get("codex_profile"),
            config_version=config_version,
            resolution_source="cli_flag",
        )

    if config.get("model") or config.get("reasoning_effort") or config.get("codex_profile"):
        return ExecutionProfile(
            model=config.get("model"),
            reasoning_effort=config.get("reasoning_effort"),
            codex_profile=config.get("codex_profile"),
            config_version=config_version,
            resolution_source="project_config",
        )

    if require_config:
        raise ConfigError(
            "E_CONFIG_MISSING", config_missing_message(worktree or "<worktree>")
        )

    # No pinned policy: record where the effective model comes from.
    source = "user_config" if _read_user_model_pin(codex_home) else "cli_default"
    return ExecutionProfile(
        model=None, reasoning_effort=None, codex_profile=None,
        config_version="none", resolution_source=source,
    )


# ---------------------------------------------------------------------------
# write_config — closed-schema TOML emitter
# ---------------------------------------------------------------------------

def _toml_string(value: str) -> str:
    # JSON basic-string escaping is valid TOML basic-string escaping.
    return json.dumps(value, ensure_ascii=True)


def write_config(
    path: str,
    *,
    model: str,
    reasoning_effort: str | None = None,
    codex_profile: str | None = None,
    force: bool = False,
) -> None:
    """Atomically write a closed-schema project config.

    Emits ONLY known keys (no catalog metadata, no timestamps — the
    version-controlled file stays declarative and diff-stable). The emitted
    text is parsed back before the atomic replace, so a partial or malformed
    write can never land. Refuses to overwrite an existing file without
    ``force``.
    """
    if os.path.exists(path) and not force:
        raise ConfigError(
            "E_CONFIG_EXISTS",
            f"E_CONFIG_EXISTS: config {path} already exists; pass --force to "
            "replace it (existing project policies are never silently "
            "overwritten).",
        )
    lines = [f"version = {SUPPORTED_CONFIG_VERSION}", f"model = {_toml_string(model)}"]
    if reasoning_effort is not None:
        lines.append(f"reasoning_effort = {_toml_string(reasoning_effort)}")
    if codex_profile is not None:
        lines.append(f"codex_profile = {_toml_string(codex_profile)}")
    text = "\n".join(lines) + "\n"

    # Parse-back validation: never let an unrepresentable value corrupt the file.
    tomllib.loads(text)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
