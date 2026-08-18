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
import tempfile
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

# \Z (not $): $ would accept a trailing newline, which would then flow into
# argv and the -c TOML-value quoting as an invalid character.
_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z", re.IGNORECASE)


def validate_policy_values(
    model: str | None, reasoning_effort: str | None = None,
    codex_profile: str | None = None,
) -> None:
    """Validate policy VALUES at the seam that writes or forwards them.

    ``load_config`` re-checks everything on read; this guards the write side
    (``init-config`` / ``write_config``) so a bootstrap command can never
    emit a config its own loader rejects. Raises ``ConfigError``.
    """
    if model is not None and model != CLI_DEFAULT and not _MODEL_ID_RE.match(model):
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: model must be a model id or '{CLI_DEFAULT}', "
            f"got {model!r}.",
        )
    if reasoning_effort is not None and reasoning_effort not in VALID_REASONING_EFFORTS:
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: reasoning effort must be one of "
            f"{VALID_REASONING_EFFORTS}, got {reasoning_effort!r}.",
        )
    if codex_profile is not None and not _MODEL_ID_RE.match(codex_profile):
        raise ConfigError(
            "E_CONFIG_MALFORMED",
            f"E_CONFIG_MALFORMED: codex_profile must be a profile name "
            f"(letters/digits/dots/dashes), got {codex_profile!r}.",
        )


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
        f"{worktree} and --require-config is set (CI mode).\n"
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
    # type(...) is int rejects bools (True == 1) and floats (1.0 == 1), which
    # would otherwise pass the equality check yet change the profile digest.
    if type(version) is not int or version != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            "E_CONFIG_UNSUPPORTED_VERSION",
            f"E_CONFIG_MALFORMED: config {path} version must be the integer "
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
    # Shape-check values at load too (not just type): an invalid profile name
    # must fail as a stable config error here, not as a traceback later when
    # the exec vector is built.
    validate_policy_values(
        model=data.get("model"), reasoning_effort=effort, codex_profile=codex_profile,
    )

    return data


# ---------------------------------------------------------------------------
# ExecutionProfile — one canonical identity bound into every artifact
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionProfile:
    """The canonical record of what a Codex invocation will run.

    ``digest`` hashes only the execution-affecting fields (model,
    reasoning_effort, codex_profile, config_version, native_model_pin) —
    provenance (``resolution_source``) and advisory metadata never change
    authorization. ``model`` is ``None`` when no ``-m`` is passed (the CLI
    chooses; also the normalized form of the ``cli-default`` sentinel).

    ``native_model_pin`` records the fail-soft-read user ``config.toml`` pin
    WHEN it is the effective model (no ``-m``, no project model). Binding its
    value — not just its presence — closes the drift hole where a doctor
    receipt certified under pin A still authorizes a review the CLI would run
    under pin B after the user edits their config. ``None`` when a ``-m``
    overrides, a project model is pinned, or no pin is readable (the truly
    unpinned ``cli_default`` case carries no extra binding by design).
    """

    model: str | None
    reasoning_effort: str | None
    codex_profile: str | None
    config_version: int | str  # file version, or the "none" sentinel
    resolution_source: str  # cli_flag | project_config | user_config | cli_default
    native_model_pin: str | None = None

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
                "native_model_pin": self.native_model_pin,
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
            "native_model_pin": self.native_model_pin,
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
            config_version = config["version"]  # load_config guarantees int 1

    # Read the user pin once. It is bound into the identity only when it IS
    # the effective model (no -m and no project model) — otherwise it would
    # invalidate receipts for runs it never affected.
    native_pin = _read_user_model_pin(codex_home)

    def _checked(profile: ExecutionProfile) -> ExecutionProfile:
        # Stable config errors at the resolution seam (never a traceback from
        # the exec-vector builder later).
        validate_policy_values(
            model=profile.model, reasoning_effort=profile.reasoning_effort,
            codex_profile=profile.codex_profile,
        )
        return profile

    has_cli = any(v is not None for v in (cli_model, cli_effort, cli_codex_profile))
    if has_cli:
        profile = ExecutionProfile(
            model=cli_model if cli_model is not None else config.get("model"),
            reasoning_effort=cli_effort if cli_effort is not None
            else config.get("reasoning_effort"),
            codex_profile=cli_codex_profile if cli_codex_profile is not None
            else config.get("codex_profile"),
            config_version=config_version,
            resolution_source="cli_flag",
            native_model_pin=native_pin if (
                cli_model is None and not config.get("model")
            ) else None,
        )
        # require_config gates the MODEL dimension: an effort-only flag must
        # not satisfy it while the model silently rides the native default.
        # An explicit `cli-default` DOES count as pinning the decision.
        model_pinned = cli_model is not None or bool(config.get("model"))
        if require_config and not model_pinned:
            raise ConfigError(
                "E_CONFIG_MISSING", config_missing_message(worktree or "<worktree>")
            )
        return _checked(profile)

    if config.get("model") or config.get("reasoning_effort") or config.get("codex_profile"):
        return _checked(ExecutionProfile(
            model=config.get("model"),
            reasoning_effort=config.get("reasoning_effort"),
            codex_profile=config.get("codex_profile"),
            config_version=config_version,
            resolution_source="project_config",
            native_model_pin=native_pin if not config.get("model") else None,
        ))

    if require_config:
        raise ConfigError(
            "E_CONFIG_MISSING", config_missing_message(worktree or "<worktree>")
        )

    # No pinned policy: record where the effective model comes from, and bind
    # the pin's VALUE so a later user-config edit cannot ride an old receipt.
    source = "user_config" if native_pin else "cli_default"
    return ExecutionProfile(
        model=None, reasoning_effort=None, codex_profile=None,
        config_version="none", resolution_source=source,
        native_model_pin=native_pin,
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
    # Value validation at the write seam: a bootstrap command must never emit
    # a config its own loader rejects one command later.
    validate_policy_values(model, reasoning_effort, codex_profile)
    lines = [f"version = {SUPPORTED_CONFIG_VERSION}", f"model = {_toml_string(model)}"]
    if reasoning_effort is not None:
        lines.append(f"reasoning_effort = {_toml_string(reasoning_effort)}")
    if codex_profile is not None:
        lines.append(f"codex_profile = {_toml_string(codex_profile)}")
    text = "\n".join(lines) + "\n"

    # Parse-back validation: never let an unrepresentable value corrupt the file.
    tomllib.loads(text)

    # mkstemp (not a fixed .tmp name): two concurrent init-config runs must
    # not truncate each other's temp file and install a torn config.
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)) or ".", prefix=".codex-review-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if force:
            os.replace(tmp, path)
        else:
            # Atomic no-overwrite: os.link fails with FileExistsError if a
            # concurrent run installed a config between the existence check
            # and now — the last writer must never silently replace without
            # --force. (Filesystems without hardlink support fall back to
            # the non-atomic path; the early existence check still guards
            # the common case.)
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise ConfigError(
                    "E_CONFIG_EXISTS",
                    f"E_CONFIG_EXISTS: config {path} already exists; pass "
                    "--force to replace it (existing project policies are "
                    "never silently overwritten).",
                )
            except OSError:
                os.replace(tmp, path)
            else:
                os.unlink(tmp)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
