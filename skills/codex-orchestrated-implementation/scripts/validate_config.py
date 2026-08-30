#!/usr/bin/env python3
"""Strict validator for .codex-orchestration.toml.

The TOML file is a closed project policy.  Runtime implementer/reviewer roles
are derived from the pair order; they are intentionally not config fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONFIG_VERSION = 1
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

PAIRING_MODES = frozenset({"alternating"})
PROFILES = frozenset({"lightweight", "standard", "safety-critical"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
CLAUDE_CODE_MODELS = frozenset({"fable", "opus", "sonnet", "haiku"})
PROVIDERS = frozenset({"codex", "claude_code"})

TOP_LEVEL_KEYS = frozenset({"version", "orchestrator", "pair", "workflow"})
ORCHESTRATOR_KEYS = frozenset({"provider", "model", "reasoning_effort"})
PAIR_KEYS = frozenset({"name", "provider", "model", "enabled"})
WORKFLOW_KEYS = frozenset({"profile", "pairing_mode", "first_implementer"})


class ConfigError(ValueError):
    """A loud, stable configuration error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ResolvedConfig:
    """Validated policy plus a stable digest for receipts and ledgers."""

    path: str
    policy: dict[str, Any]
    policy_digest: str


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError("E_CONFIG_SCHEMA", f"{label} must be a TOML table")
    return value


def _closed_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        if "role" in unknown:
            raise ConfigError(
                "E_ROLE_NOT_ALLOWED",
                f"{label}.role is not allowed; roles are derived per milestone",
            )
        raise ConfigError(
            "E_CONFIG_UNKNOWN_KEY",
            f"unknown key(s) in {label}: {', '.join(unknown)}",
        )


def _required(value: Mapping[str, Any], keys: frozenset[str], label: str) -> None:
    missing = sorted(keys - set(value))
    if missing:
        raise ConfigError(
            "E_CONFIG_SCHEMA",
            f"missing key(s) in {label}: {', '.join(missing)}",
        )


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("E_CONFIG_SCHEMA", f"{label} must be a non-empty string")
    return value


def _provider_model(value: Mapping[str, Any], label: str) -> None:
    provider = _string(value.get("provider"), f"{label}.provider")
    model = _string(value.get("model"), f"{label}.model")
    if provider not in PROVIDERS:
        raise ConfigError("E_PROVIDER_MODEL", f"unsupported provider {provider!r} in {label}")
    if provider == "codex":
        if MODEL_ID_RE.fullmatch(model) and model.startswith("gpt-"):
            return
        raise ConfigError(
            "E_PROVIDER_MODEL",
            f"model {model!r} is not a valid Codex model id in {label}; "
            "expected a lowercase gpt-* identifier",
        )
    if model not in CLAUDE_CODE_MODELS:
        allowed = ", ".join(sorted(CLAUDE_CODE_MODELS))
        raise ConfigError(
            "E_PROVIDER_MODEL",
            f"model {model!r} is not supported for provider 'claude_code' in {label}; "
            f"allowed aliases: {allowed}",
        )


def validate_data(data: Any) -> dict[str, Any]:
    """Validate parsed TOML and return the original policy."""

    root = _mapping(data, "root")
    _closed_keys(root, TOP_LEVEL_KEYS, "root")
    _required(root, TOP_LEVEL_KEYS, "root")

    if type(root["version"]) is not int or root["version"] != CONFIG_VERSION:
        raise ConfigError(
            "E_CONFIG_SCHEMA",
            f"version must be the integer {CONFIG_VERSION}",
        )

    orchestrator = _mapping(root["orchestrator"], "orchestrator")
    _closed_keys(orchestrator, ORCHESTRATOR_KEYS, "orchestrator")
    _required(orchestrator, ORCHESTRATOR_KEYS, "orchestrator")
    _provider_model(orchestrator, "orchestrator")
    if orchestrator["provider"] != "codex":
        raise ConfigError(
            "E_PROVIDER_MODEL",
            "orchestrator.provider must be 'codex'; the resolved Terra/orchestrator "
            "profile is confirmed at runtime, not by a prompt or TOML value",
        )
    effort = _string(orchestrator["reasoning_effort"], "orchestrator.reasoning_effort")
    if effort not in REASONING_EFFORTS:
        raise ConfigError(
            "E_CONFIG_SCHEMA",
            "orchestrator.reasoning_effort must be one of "
            + ", ".join(sorted(REASONING_EFFORTS)),
        )

    pair = root["pair"]
    if not isinstance(pair, list) or len(pair) != 2:
        raise ConfigError(
            "E_PAIR_CARDINALITY",
            "pair must contain exactly two [[pair]] members; both must be enabled",
        )
    names: list[str] = []
    for index, member_value in enumerate(pair):
        label = f"pair[{index}]"
        member = _mapping(member_value, label)
        _closed_keys(member, PAIR_KEYS, label)
        _required(member, PAIR_KEYS, label)
        name = _string(member["name"], f"{label}.name")
        if not NAME_RE.fullmatch(name):
            raise ConfigError("E_CONFIG_SCHEMA", f"{label}.name is not a valid member name")
        names.append(name)
        if member["enabled"] is not True:
            raise ConfigError(
                "E_PAIR_CARDINALITY",
                f"{label}.enabled must be true; disabled or extra workers are not supported",
            )
        _provider_model(member, label)
    if len(set(names)) != 2:
        raise ConfigError("E_PAIR_DUPLICATE", "pair member names must be unique")

    workflow = _mapping(root["workflow"], "workflow")
    _closed_keys(workflow, WORKFLOW_KEYS, "workflow")
    _required(workflow, WORKFLOW_KEYS, "workflow")
    profile = _string(workflow["profile"], "workflow.profile")
    if profile not in PROFILES:
        raise ConfigError("E_CONFIG_SCHEMA", f"unsupported workflow.profile {profile!r}")
    pairing_mode = _string(workflow["pairing_mode"], "workflow.pairing_mode")
    if pairing_mode not in PAIRING_MODES:
        raise ConfigError(
            "E_PAIRING_MODE",
            f"unsupported pairing_mode {pairing_mode!r}; only 'alternating' is supported",
        )
    first = _string(workflow["first_implementer"], "workflow.first_implementer")
    if first not in names:
        raise ConfigError(
            "E_FIRST_IMPLEMENTER",
            f"first_implementer {first!r} must name exactly one pair member",
        )
    return dict(root)


def load_config(path: str | Path) -> ResolvedConfig:
    """Parse and strictly validate a project TOML file."""

    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("E_CONFIG_READ", f"cannot read {config_path}: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("E_CONFIG_MALFORMED", f"invalid TOML in {config_path}: {exc}") from exc
    policy = validate_data(data)
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ResolvedConfig(str(config_path.resolve()), policy, digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a closed orchestration TOML policy")
    parser.add_argument("--config", required=True, help="project .codex-orchestration.toml")
    args = parser.parse_args(argv)
    try:
        resolved = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "source": "project_config",
                "config": resolved.path,
                "policy_digest": resolved.policy_digest,
                "policy": resolved.policy,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
