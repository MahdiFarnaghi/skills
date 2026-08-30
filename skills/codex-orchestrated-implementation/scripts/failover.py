"""Fail-closed validation for Terra-approved Claude worker failover."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from validate_config import ResolvedConfig

REASONS = frozenset({"quota_exhausted", "transport_failure", "authentication_failure", "timeout", "tool_failure"})
FIELDS = frozenset({"schema_version", "record_type", "transition_id", "idempotency_key", "task_id", "milestone_id", "milestone_index", "failed_role", "replacement_role", "transition_kind", "failure_reason", "failed_invocation_id", "terra_decision_id", "approved_by", "replacement_role_instance_id", "replacement_session_id", "replacement_context_id", "path_scope", "target_fingerprint", "status"})

class FailoverError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")

def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FailoverError("E_FAILOVER_SCHEMA", f"{label} must be non-empty text")
    return value

def validate_transition(config: ResolvedConfig, transition: Any) -> Mapping[str, Any]:
    if not isinstance(transition, Mapping):
        raise FailoverError("E_FAILOVER_SCHEMA", "transition must be an object")
    missing, unknown = FIELDS - set(transition), set(transition) - FIELDS
    if missing or unknown:
        raise FailoverError("E_FAILOVER_SCHEMA", f"missing={sorted(missing)} unknown={sorted(unknown)}")
    policy = config.policy.get("failover", {})
    if not policy.get("enabled") or not policy.get("allow_luna_worker_fallback"):
        raise FailoverError("E_FAILOVER_DISABLED", "worker failover is disabled by project policy")
    if transition["schema_version"] != 2 or transition["record_type"] != "role_transition":
        raise FailoverError("E_FAILOVER_SCHEMA", "failover transitions require schema v2")
    for field in ("transition_id", "idempotency_key", "task_id", "milestone_id", "failed_invocation_id", "terra_decision_id", "approved_by", "replacement_role_instance_id", "replacement_session_id", "replacement_context_id"):
        _text(transition[field], field)
    if type(transition["milestone_index"]) is not int or transition["milestone_index"] < 1:
        raise FailoverError("E_FAILOVER_SCHEMA", "milestone_index must be >= 1")
    if transition["failed_role"] != "claude_worker" or transition["replacement_role"] != "luna_worker":
        raise FailoverError("E_FAILOVER_ROLE", "only Claude-to-Luna failover is supported")
    if transition["transition_kind"] not in {"reviewer_fallback", "implementation_takeover"}:
        raise FailoverError("E_FAILOVER_ROLE", "unsupported transition kind")
    if transition["failure_reason"] not in REASONS:
        raise FailoverError("E_FAILOVER_REASON", "unsupported worker failure reason")
    if transition["approved_by"] != "terra":
        raise FailoverError("E_FAILOVER_AUTHORITY", "Terra approval is required for failover")
    if not isinstance(transition["path_scope"], list) or not transition["path_scope"] or any(not isinstance(p, str) or not p for p in transition["path_scope"]):
        raise FailoverError("E_FAILOVER_SCHEMA", "path_scope must be a non-empty path list")
    fingerprint = _text(transition["target_fingerprint"], "target_fingerprint")
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise FailoverError("E_FAILOVER_SCHEMA", "target_fingerprint must be a SHA-256 digest")
    if transition["status"] != "approved":
        raise FailoverError("E_FAILOVER_STATE", "transition status must be approved")
    return transition

def append_transition(path: str, config: ResolvedConfig, transition: Mapping[str, Any]) -> None:
    record = validate_transition(config, transition)
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "a+", encoding="utf-8") as handle:
        handle.seek(0)
        for line in handle:
            existing = json.loads(line)
            if existing.get("idempotency_key") == record["idempotency_key"]:
                old = hashlib.sha256(json.dumps(existing, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if old != digest:
                    raise FailoverError("E_FAILOVER_IDEMPOTENCY_CONFLICT", "idempotency key was reused with different content")
                return
        handle.write(canonical + "\n")
