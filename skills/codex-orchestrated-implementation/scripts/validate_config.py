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
CONFIG_V2_VERSION = 2
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

PAIRING_MODES = frozenset({"alternating"})
PROFILES = frozenset({"lightweight", "standard", "safety-critical"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
CLAUDE_CODE_MODELS = frozenset({"fable", "opus", "sonnet", "haiku"})
PROVIDERS = frozenset({"codex", "claude_code"})
QWEN_PROVIDER = "qwen_local"

TOP_LEVEL_KEYS = frozenset({"version", "orchestrator", "pair", "workflow"})
V2_TOP_LEVEL_KEYS = frozenset({"version", "technical_authority", "orchestrator", "pair", "workflow", "qwen_local", "failover"})
ORCHESTRATOR_KEYS = frozenset({"provider", "model", "reasoning_effort"})
V2_CONTROL_PLANE_KEYS = frozenset({"provider", "model", "reasoning_effort"})
PAIR_KEYS = frozenset({"name", "provider", "model", "enabled"})
WORKFLOW_KEYS = frozenset({"profile", "pairing_mode", "first_implementer"})
QWEN_KEYS = frozenset({
    "enabled", "endpoint", "allowed_endpoints", "model", "allowed_models", "tls_policy",
    "auth_mode", "auth_token_env", "max_context_tokens", "max_input_tokens", "max_output_tokens",
    "reasoning_effort", "timeout_seconds", "max_retries", "tools", "skills", "inherit_credentials",
    "write_mode",
})
QWEN_REASONING_EFFORTS = frozenset({"low", "medium"})
QWEN_TLS_POLICIES = frozenset({"required", "localhost_insecure"})
QWEN_AUTH_MODES = frozenset({"none", "explicit_env"})
QWEN_WRITE_MODES = frozenset({"disabled", "draft_patch"})
FAILOVER_KEYS = frozenset({"enabled", "allow_luna_worker_fallback", "require_terra_approval", "fallback_reviewer", "max_fallback_rounds"})


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


def _validate_v2_qwen(qwen: Mapping[str, Any]) -> None:
    _closed_keys(qwen, QWEN_KEYS, "qwen_local")
    _required(qwen, QWEN_KEYS, "qwen_local")
    if type(qwen["enabled"]) is not bool:
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.enabled must be boolean")
    endpoint = _string(qwen["endpoint"], "qwen_local.endpoint")
    endpoints = qwen["allowed_endpoints"]
    models = qwen["allowed_models"]
    if (not isinstance(endpoints, list) or not endpoints or
            any(not isinstance(item, str) or not item for item in endpoints) or
            len(set(endpoints)) != len(endpoints) or endpoint not in endpoints):
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.allowed_endpoints must uniquely include endpoint")
    model = _string(qwen["model"], "qwen_local.model")
    if (not isinstance(models, list) or not models or
            any(not isinstance(item, str) or not item for item in models) or
            len(set(models)) != len(models) or model not in models):
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.allowed_models must uniquely include model")
    if model != "Qwen3.8-27B":
        raise ConfigError("E_QWEN_MODEL_NOT_ALLOWED", "qwen_local.model must be Qwen3.8-27B")
    if any(not item.startswith(("http://", "https://")) for item in endpoints):
        raise ConfigError("E_QWEN_CONFIG", "qwen_local endpoints must use http:// or https://")
    tls_policy = _string(qwen["tls_policy"], "qwen_local.tls_policy")
    if tls_policy not in QWEN_TLS_POLICIES:
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.tls_policy must be required or localhost_insecure")
    from urllib.parse import urlparse
    for item in endpoints:
        parsed = urlparse(item)
        if not parsed.netloc or parsed.query or parsed.fragment:
            raise ConfigError("E_QWEN_CONFIG", f"qwen_local endpoint {item!r} is not a safe base URL")
        if parsed.scheme == "http" and (tls_policy != "localhost_insecure" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ConfigError("E_QWEN_TLS_REQUIRED", "unencrypted Qwen endpoints are allowed only for localhost_insecure")
    auth_mode = _string(qwen["auth_mode"], "qwen_local.auth_mode")
    if auth_mode not in QWEN_AUTH_MODES:
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.auth_mode must be none or explicit_env")
    token_env = qwen["auth_token_env"]
    if not isinstance(token_env, str) or (auth_mode == "none" and token_env) or (auth_mode == "explicit_env" and not re.fullmatch(r"[A-Z][A-Z0-9_]*", token_env)):
        raise ConfigError("E_QWEN_AUTH_POLICY", "auth_token_env must be empty for none or an explicit uppercase env name")
    if qwen["tls_policy"] == "required" and any(item.startswith("http://") for item in endpoints):
        raise ConfigError("E_QWEN_TLS_REQUIRED", "qwen_local.tls_policy=required forbids http endpoints")
    for key, minimum, maximum in (("max_context_tokens", 256, 32768), ("max_input_tokens", 1, 32768), ("max_output_tokens", 1, 8192), ("timeout_seconds", 1, 120), ("max_retries", 0, 3)):
        value = qwen[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ConfigError("E_QWEN_BUDGET", f"qwen_local.{key} must be an integer in [{minimum}, {maximum}]")
    if qwen["max_input_tokens"] + qwen["max_output_tokens"] > qwen["max_context_tokens"]:
        raise ConfigError("E_QWEN_BUDGET", "Qwen input plus output budgets exceed max_context_tokens")
    effort = _string(qwen["reasoning_effort"], "qwen_local.reasoning_effort")
    if effort not in QWEN_REASONING_EFFORTS:
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.reasoning_effort must be low or medium")
    for key in ("tools", "skills", "inherit_credentials"):
        if qwen[key] is not False:
            raise ConfigError("E_QWEN_CAPABILITY_POLICY", f"qwen_local.{key} must be false")
    write_mode = _string(qwen["write_mode"], "qwen_local.write_mode")
    if write_mode not in QWEN_WRITE_MODES:
        raise ConfigError("E_QWEN_CONFIG", "qwen_local.write_mode must be disabled or draft_patch")
    if not qwen["enabled"] and write_mode != "disabled":
        raise ConfigError("E_QWEN_WRITE_POLICY", "disabled Qwen assistant must have write_mode=disabled")


def _validate_v2_failover(failover: Mapping[str, Any]) -> None:
    _closed_keys(failover, FAILOVER_KEYS, "failover")
    _required(failover, FAILOVER_KEYS, "failover")
    for key in ("enabled", "allow_luna_worker_fallback", "require_terra_approval"):
        if type(failover[key]) is not bool:
            raise ConfigError("E_FAILOVER_CONFIG", f"failover.{key} must be boolean")
    if failover["fallback_reviewer"] != "luna_worker":
        raise ConfigError("E_FAILOVER_ROLE", "failover.fallback_reviewer must be luna_worker")
    if type(failover["max_fallback_rounds"]) is not int or not 0 <= failover["max_fallback_rounds"] <= 1:
        raise ConfigError("E_FAILOVER_LIMIT", "failover.max_fallback_rounds must be 0 or 1")
    if failover["allow_luna_worker_fallback"] and not failover["require_terra_approval"]:
        raise ConfigError("E_FAILOVER_AUTHORITY", "Luna fallback always requires Terra approval")


def _validate_v2(root: Mapping[str, Any]) -> dict[str, Any]:
    _closed_keys(root, V2_TOP_LEVEL_KEYS, "root")
    _required(root, frozenset({"version", "technical_authority", "orchestrator", "pair", "workflow"}), "root")
    if type(root["version"]) is not int or root["version"] != CONFIG_V2_VERSION:
        raise ConfigError("E_CONFIG_SCHEMA", "version must be the integer 2")
    authority = _mapping(root["technical_authority"], "technical_authority")
    _closed_keys(authority, V2_CONTROL_PLANE_KEYS, "technical_authority")
    _required(authority, V2_CONTROL_PLANE_KEYS, "technical_authority")
    _provider_model(authority, "technical_authority")
    if authority["provider"] != "codex" or not authority["model"].endswith("-terra"):
        raise ConfigError("E_CONTROL_PLANE_ROLE", "technical_authority must be a Codex Terra model")
    if _string(authority["reasoning_effort"], "technical_authority.reasoning_effort") not in REASONING_EFFORTS:
        raise ConfigError("E_CONFIG_SCHEMA", "technical_authority.reasoning_effort is unsupported")
    orchestrator = _mapping(root["orchestrator"], "orchestrator")
    _closed_keys(orchestrator, V2_CONTROL_PLANE_KEYS, "orchestrator")
    _required(orchestrator, V2_CONTROL_PLANE_KEYS, "orchestrator")
    _provider_model(orchestrator, "orchestrator")
    if orchestrator["provider"] != "codex" or not orchestrator["model"].endswith("-luna"):
        raise ConfigError("E_CONTROL_PLANE_ROLE", "orchestrator must be a Codex Luna model")
    if _string(orchestrator["reasoning_effort"], "orchestrator.reasoning_effort") not in REASONING_EFFORTS:
        raise ConfigError("E_CONFIG_SCHEMA", "orchestrator.reasoning_effort is unsupported")
    pair = root["pair"]
    if not isinstance(pair, list) or len(pair) != 2:
        raise ConfigError("E_PAIR_CARDINALITY", "v2 pair must contain exactly luna_worker and claude_worker")
    names: list[str] = []
    for index, member_value in enumerate(pair):
        label = f"pair[{index}]"
        member = _mapping(member_value, label)
        _closed_keys(member, PAIR_KEYS, label)
        _required(member, PAIR_KEYS, label)
        name = _string(member["name"], f"{label}.name")
        if name not in {"luna_worker", "claude_worker"}:
            raise ConfigError("E_PAIR_ROLE", "v2 pair names must be luna_worker and claude_worker")
        names.append(name)
        if member["enabled"] is not True:
            raise ConfigError("E_PAIR_CARDINALITY", "v2 pair members must both be enabled")
        _provider_model(member, label)
        if name == "luna_worker" and (member["provider"] != "codex" or not member["model"].endswith("-luna")):
            raise ConfigError("E_PAIR_ROLE", "luna_worker must use a Codex Luna model")
        if name == "claude_worker" and member["provider"] != "claude_code":
            raise ConfigError("E_PAIR_ROLE", "claude_worker must use claude_code")
    if set(names) != {"luna_worker", "claude_worker"}:
        raise ConfigError("E_PAIR_ROLE", "v2 pair must contain each named worker exactly once")
    workflow = _mapping(root["workflow"], "workflow")
    _closed_keys(workflow, WORKFLOW_KEYS, "workflow")
    _required(workflow, WORKFLOW_KEYS, "workflow")
    if _string(workflow["profile"], "workflow.profile") not in PROFILES:
        raise ConfigError("E_CONFIG_SCHEMA", "unsupported workflow.profile")
    if _string(workflow["pairing_mode"], "workflow.pairing_mode") != "alternating":
        raise ConfigError("E_PAIRING_MODE", "v2 pairing_mode must be alternating")
    if _string(workflow["first_implementer"], "workflow.first_implementer") not in names:
        raise ConfigError("E_FIRST_IMPLEMENTER", "first_implementer must name a v2 pair member")
    if "qwen_local" in root:
        _validate_v2_qwen(_mapping(root["qwen_local"], "qwen_local"))
    else:
        root = dict(root)
        root["qwen_local"] = {
            "enabled": False, "endpoint": "https://127.0.0.1:8000/v1", "allowed_endpoints": ["https://127.0.0.1:8000/v1"],
            "model": "Qwen3.8-27B", "allowed_models": ["Qwen3.8-27B"], "tls_policy": "required", "auth_mode": "explicit_env", "auth_token_env": "LLM_BEARER_TOKEN",
            "max_context_tokens": 4096, "max_input_tokens": 2048, "max_output_tokens": 1024, "reasoning_effort": "low", "timeout_seconds": 20, "max_retries": 0,
            "tools": False, "skills": False, "inherit_credentials": False, "write_mode": "disabled",
        }
    if "failover" in root:
        _validate_v2_failover(_mapping(root["failover"], "failover"))
    else:
        root = dict(root)
        root["failover"] = {"enabled": False, "allow_luna_worker_fallback": False, "require_terra_approval": True, "fallback_reviewer": "luna_worker", "max_fallback_rounds": 0}
    return dict(root)


def validate_data(data: Any) -> dict[str, Any]:
    """Validate parsed TOML and return the original policy."""

    root = _mapping(data, "root")
    if root.get("version") == CONFIG_V2_VERSION:
        return _validate_v2(root)
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
