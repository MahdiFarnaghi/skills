#!/usr/bin/env python3
"""Closed host-continuation protocol for Luna.

The guard is intentionally host-agnostic.  It validates records and persists
the lease/side-effect boundary; Luna (or an injected test adapter) performs
the actual Codex/Claude calls.  No model API is invoked by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from orchestration_state import OrchestrationStore, StateError, TERMINAL_STATES
from validate_handoff import HandoffError, _receipt

ACTION_NAMES = frozenset({
    "dispatch_codex", "dispatch_claude", "wait_codex", "wait_claude",
    "recover_receipt", "validate_receipt", "run_verification",
    "request_terra_decision", "register_wakeup", "reconcile_job",
    "final_allowed",
})
HOST_STATUSES = frozenset({"queued", "running", "completed", "needs_attention", "failed", "cancelled", "unknown"})
DISPATCH_ACTIONS = frozenset({"dispatch_codex", "dispatch_claude"})
RESULT_KEYS = frozenset({
    "host_job_id", "status", "cursor", "progress", "completion_reason",
    "receipt_reference", "user_attention_required", "action_id", "invocation_id",
    "idempotency_key", "error", "wakeup_required",
})
TRUSTED_RECEIPT_KEYS = frozenset({"host_job_id", "receipt_reference", "idempotency_key", "finding_ids"})
RECEIPT_OUTCOMES = frozenset({"success", "failure", "transport_failure", "needs_correction", "approve", "escalate"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GuardError(ValueError):
    """Stable fail-closed protocol error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _timestamp(value: Any = None) -> str:
    if value is not None:
        if isinstance(value, str):
            return value
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GuardError("E_GUARD_SCHEMA", f"invalid timestamp {value!r}") from exc


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GuardError("E_GUARD_SCHEMA", f"{label} must be a non-empty string")
    return value


def validate_host_result(value: Any) -> dict[str, Any]:
    """Validate and normalize one host adapter result."""
    if not isinstance(value, dict):
        raise GuardError("E_HOST_RESULT_SCHEMA", "host result must be an object")
    unknown = set(value) - RESULT_KEYS
    if unknown:
        raise GuardError("E_HOST_RESULT_UNKNOWN_FIELD", f"unknown host result field(s): {', '.join(sorted(unknown))}")
    required = ("host_job_id", "status", "cursor", "progress", "completion_reason", "receipt_reference", "user_attention_required")
    missing = [key for key in required if key not in value]
    if missing:
        raise GuardError("E_HOST_RESULT_SCHEMA", f"missing host result field(s): {', '.join(missing)}")
    result = dict(value)
    _require_string(result["host_job_id"], "host_job_id")
    if result["status"] not in HOST_STATUSES:
        raise GuardError("E_HOST_RESULT_STATUS", f"unsupported host status {result['status']!r}")
    if result["cursor"] is not None and not isinstance(result["cursor"], str):
        raise GuardError("E_HOST_RESULT_SCHEMA", "cursor must be a string or null")
    if not isinstance(result["completion_reason"], str):
        raise GuardError("E_HOST_RESULT_SCHEMA", "completion_reason must be a string")
    if result["receipt_reference"] is not None and not isinstance(result["receipt_reference"], str):
        raise GuardError("E_HOST_RESULT_SCHEMA", "receipt_reference must be a string or null")
    if type(result["user_attention_required"]) is not bool:
        raise GuardError("E_HOST_RESULT_SCHEMA", "user_attention_required must be boolean")
    if "wakeup_required" in result and type(result["wakeup_required"]) is not bool:
        raise GuardError("E_HOST_RESULT_SCHEMA", "wakeup_required must be boolean")
    # A tool call returning or timing out is never completion.  Adapters must
    # explicitly use running/unknown, which also keeps retries idempotent.
    return result


def validate_host_action(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GuardError("E_HOST_ACTION_SCHEMA", "host action must be an object")
    action = _require_string(value.get("action"), "action")
    if action not in ACTION_NAMES:
        raise GuardError("E_HOST_ACTION_UNKNOWN", f"unknown host action {action!r}")
    allowed = {"action", "task_id", "milestone_id", "milestone_index", "round", "state_generation", "lease_id", "job_id", "cursor", "provider", "role", "phase", "role_instance_id", "session_id", "context_id", "host_id", "resolved_profile", "resolved_orchestrator_profile", "policy_digest", "worktree", "path_scope", "prompt_artifact", "invocation_id", "idempotency_key", "timeout_seconds", "receipt_location", "earliest_poll_at", "hard_deadline", "reason"}
    unknown = set(value) - allowed
    if unknown:
        raise GuardError("E_HOST_ACTION_UNKNOWN_FIELD", f"unknown host action field(s): {', '.join(sorted(unknown))}")
    if action in DISPATCH_ACTIONS:
        for key in ("provider", "role", "worktree", "prompt_artifact", "invocation_id", "idempotency_key", "receipt_location"):
            _require_string(value.get(key), f"action.{key}")
        if value["provider"] not in {"codex", "claude_code"}:
            raise GuardError("E_HOST_ACTION_PROVIDER", "dispatch provider must be codex or claude_code")
        if not isinstance(value.get("path_scope"), list) or not value["path_scope"] or any(not isinstance(item, str) or not item for item in value["path_scope"]):
            raise GuardError("E_HOST_ACTION_SCOPE", "dispatch path_scope must be a non-empty list of paths")
        if type(value.get("timeout_seconds")) is not int or value["timeout_seconds"] <= 0:
            raise GuardError("E_HOST_ACTION_TIMEOUT", "dispatch timeout_seconds must be positive")
    elif action in {"wait_codex", "wait_claude", "reconcile_job"}:
        if type(value.get("timeout_seconds")) is not int or not 0 < value["timeout_seconds"] <= 60:
            raise GuardError("E_HOST_ACTION_TIMEOUT", "bounded host waits must be between 1 and 60 seconds")
    return dict(value)


def _receipt_error(exc: Exception) -> GuardError:
    return GuardError("E_RECEIPT_CONTRACT", str(exc))


def _trusted_receipt(receipt: Any, state: Mapping[str, Any], *, host_job_id: str, reference: str) -> dict[str, Any]:
    """Validate a worker receipt against the persisted dispatch contract.

    The worker receipt is evidence, not a caller-provided verdict.  The
    persisted dispatch intent is the authority for all identity and scope
    comparisons; only a receipt that matches it can enter the routing state.
    """
    try:
        value = dict(_receipt(receipt, "worker receipt"))
    except (HandoffError, TypeError, ValueError) as exc:
        raise _receipt_error(exc) from exc
    if value.get("schema_version") != 2:
        raise GuardError("E_RECEIPT_CONTRACT", "Phase 4 requires a v2 worker receipt")
    for key in TRUSTED_RECEIPT_KEYS:
        if key not in value:
            raise GuardError("E_RECEIPT_CONTRACT", f"worker receipt missing trusted field {key}")
    if not isinstance(value["finding_ids"], list) or any(not isinstance(item, str) or not item for item in value["finding_ids"]):
        raise GuardError("E_RECEIPT_CONTRACT", "worker receipt finding_ids must be non-empty strings")
    if value["host_job_id"] != host_job_id or value["receipt_reference"] != reference:
        raise GuardError("E_RECEIPT_BINDING", "receipt host job or receipt reference does not match the completed host result")
    if value["outcome"] not in RECEIPT_OUTCOMES:
        raise GuardError("E_RECEIPT_OUTCOME", "receipt outcome is not routable")

    intent = state.get("last_dispatch_intent") or state.get("pending_dispatch") or {}
    expected = {
        "task_id": state.get("task_id"), "milestone_id": state.get("milestone_id"),
        "milestone_index": state.get("milestone_index"), "round": state.get("round"),
        "invocation_id": intent.get("invocation_id"), "idempotency_key": intent.get("idempotency_key"),
        "pair_member": intent.get("role"), "provider": intent.get("provider"),
        "model": (intent.get("resolved_profile") or {}).get("model"),
        "worktree": intent.get("worktree") or state.get("worktree"),
        "path_scope": intent.get("path_scope") or state.get("path_scope"),
        "policy_digest": intent.get("policy_digest") or state.get("policy_digest"),
        "resolved_orchestrator_profile": intent.get("resolved_orchestrator_profile") or state.get("resolved_orchestrator_profile"),
        "role_instance_id": intent.get("role_instance_id"), "session_id": intent.get("session_id"),
        "host_id": intent.get("host_id"), "context_id": intent.get("context_id"),
        "resolved_profile": intent.get("resolved_profile"),
    }
    for key, actual in expected.items():
        if actual is None or (isinstance(actual, str) and not actual):
            raise GuardError("E_RECEIPT_CONTRACT", f"persisted dispatch contract lacks {key}")
        if value.get(key) != actual:
            raise GuardError("E_RECEIPT_BINDING", f"receipt {key} does not match the persisted dispatch contract")
    if state.get("baseline") is not None and value.get("baseline") != state["baseline"]:
        raise GuardError("E_RECEIPT_BINDING", "receipt baseline does not match the persisted worktree baseline")
    phase = intent.get("phase", "")
    expected_kind = "review" if phase in {"review", "rereview"} else "implementation"
    expected_role = "reviewer" if expected_kind == "review" else "implementer"
    if value.get("invocation_kind") != expected_kind or value.get("role") != expected_role:
        raise GuardError("E_RECEIPT_BINDING", "receipt invocation kind or role does not match the dispatch phase")
    expected_read_only = expected_kind == "review"
    if value.get("read_only") is not expected_read_only:
        raise GuardError("E_RECEIPT_POLICY", "receipt read-only guarantee does not match the dispatch phase")
    if expected_read_only:
        if value["target_fingerprint_before"] != value["target_fingerprint_after"]:
            raise GuardError("E_RECEIPT_POLICY", "review receipt changed the declared target")
        if value.get("full_worktree_fingerprint_before") != value.get("full_worktree_fingerprint_after"):
            raise GuardError("E_RECEIPT_POLICY", "review receipt changed the full worktree")
    for key in ("target_fingerprint_before", "target_fingerprint_after"):
        if not HEX64.fullmatch(value[key]):
            raise GuardError("E_RECEIPT_CONTRACT", f"receipt {key} is not a SHA-256 fingerprint")
    return value


class ContinuationGuard:
    """Durable lease and host-record boundary around :class:`OrchestrationStore`."""

    def __init__(self, store: OrchestrationStore | str | Path, *, clock: Callable[[], float] | None = None, lease_duration_seconds: int = 120, max_reconciliation_attempts: int = 3):
        self.store = store if isinstance(store, OrchestrationStore) else OrchestrationStore(store)
        self.clock = clock or time.time
        self.lease_duration_seconds = lease_duration_seconds
        self.max_reconciliation_attempts = max_reconciliation_attempts

    def _now(self) -> str:
        return _timestamp(self.clock())

    def state(self) -> dict[str, Any]:
        state = self.store.read()
        if state is None:
            raise GuardError("E_STATE_MISSING", "orchestration state has not been created")
        return state

    def acquire_lease(self, *, lease_id: str, owner_role_instance_id: str, host_thread_id: str, wait_cursor: str | None = None) -> dict[str, Any]:
        _require_string(lease_id, "lease_id"); _require_string(owner_role_instance_id, "owner_role_instance_id"); _require_string(host_thread_id, "host_thread_id")
        state = self.state()
        lease = state.get("continuation_lease")
        if lease and lease.get("lease_id") == lease_id and lease.get("owner_role_instance_id") == owner_role_instance_id and lease.get("status") in {"active", "handoff_pending"}:
            return state
        if lease and lease.get("status") == "handoff_pending":
            raise GuardError("E_LEASE_HANDOFF", "another continuation is already adopting the persisted host job")
        if lease and lease.get("status") == "active" and lease.get("expires_at"):
            if _parse_time(lease["expires_at"]) > _parse_time(self._now()) and lease.get("lease_id") != lease_id:
                raise GuardError("E_LEASE_HELD", "another Luna continuation lease is active")
        now = self._now()
        expires = _timestamp(self.clock() + self.lease_duration_seconds)
        return self.store.transition("lease_acquired", lease_id=lease_id, owner_role_instance_id=owner_role_instance_id, host_thread_id=host_thread_id, wait_cursor=wait_cursor, timestamp=now, expires_at=expires)

    def _check_lease(self, lease_id: str, generation: int | None = None) -> dict[str, Any]:
        state = self.state(); lease = state.get("continuation_lease") or {}
        if lease.get("lease_id") != lease_id or lease.get("status") not in {"active", "handoff_pending"}:
            raise GuardError("E_LEASE_OWNER", "operation is not owned by the active continuation lease")
        if lease.get("status") == "active" and lease.get("expires_at") and _parse_time(lease["expires_at"]) <= _parse_time(self._now()):
            raise GuardError("E_LEASE_EXPIRED", "continuation lease has expired; acquire a new lease before acting")
        if generation is not None and generation != lease.get("generation"):
            raise GuardError("E_LEASE_GENERATION", "stale continuation lease generation")
        return lease

    def heartbeat(self, *, lease_id: str) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        if lease.get("status") == "handoff_pending":
            raise GuardError("E_LEASE_HANDOFF_REQUIRED", "reconcile the adopted host job before heartbeating")
        now = self._now()
        return self.store.transition("lease_heartbeat", lease_id=lease_id, generation=lease.get("generation"), expected_lease_generation=lease.get("generation"), timestamp=now, expires_at=_timestamp(self.clock() + self.lease_duration_seconds))

    def record_dispatch_intent(self, action: Mapping[str, Any]) -> dict[str, Any]:
        action = validate_host_action(action)
        if action["action"] not in DISPATCH_ACTIONS:
            raise GuardError("E_GUARD_ACTION", "only dispatch actions may be recorded as dispatch intent")
        lease = self._check_lease(action.get("lease_id", ""), action.get("state_generation"))
        if lease.get("status") == "handoff_pending":
            raise GuardError("E_LEASE_HANDOFF_REQUIRED", "reconcile the adopted host job before dispatching")
        pending = self.state().get("pending_dispatch") or {}
        if pending.get("intent_digest") == digest(action):
            return self.state()
        intent = dict(action)
        intent["intent_digest"] = digest(action)
        return self.store.transition("dispatch_intent", intent=intent, invocation_id=action["invocation_id"], idempotency_key=action["idempotency_key"], expected_lease_generation=lease["generation"], timestamp=self._now())

    def record_dispatch_result(self, *, lease_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        if lease.get("status") == "handoff_pending":
            raise GuardError("E_LEASE_HANDOFF_REQUIRED", "reconcile the adopted host job before recording dispatch")
        result = validate_host_result(result)
        state = self.state(); intent = state.get("pending_dispatch")
        if not intent:
            if state.get("active", {}).get("job_id") == result["host_job_id"]:
                return state
            raise GuardError("E_DISPATCH_INTENT", "dispatch result has no persisted intent")
        for field in ("invocation_id", "idempotency_key"):
            supplied = result.get(field)
            if supplied is not None and supplied != intent.get(field):
                raise GuardError("E_DISPATCH_IDENTITY", f"dispatch result {field} does not match the persisted intent")
        if state.get("last_host_result") == result and state.get("pending_dispatch") == intent:
            return state
        self.store.transition("dispatch_result", result=result, intent=dict(intent), lease_id=lease_id, host_job_id=result["host_job_id"], expected_lease_generation=lease["generation"], timestamp=self._now())
        if result["status"] == "unknown":
            return self.state()
        phase = intent.get("phase") or {"dispatch_codex": "implementation", "dispatch_claude": "review"}.get(intent["action"])
        if phase is None:
            phase = "correction" if self.state()["state"] == "correction_dispatching" else "rereview" if self.state()["state"] == "rereview_dispatching" else "implementation"
        if result["status"] in {"failed", "cancelled"}:
            return self.store.transition("dispatch_failed", phase=phase, reason=result.get("completion_reason", result.get("error", "dispatch failed")), invocation_id=intent["invocation_id"], job_id=result["host_job_id"])
        return self.store.transition("dispatch", phase=phase, job_id=result["host_job_id"], invocation_id=intent["invocation_id"], provider=intent.get("provider"), role_instance_id=intent.get("role_instance_id"), session_id=intent.get("session_id"), context_id=intent.get("context_id"), host_id=intent.get("host_id"), expected_lease_generation=lease["generation"])

    def record_wait_result(self, *, lease_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        if lease.get("status") == "handoff_pending":
            raise GuardError("E_LEASE_HANDOFF_REQUIRED", "reconcile the adopted host job before waiting")
        result = validate_host_result(result)
        state = self.state(); active = state.get("active") or {}
        if active.get("job_id") and active["job_id"] != result["host_job_id"]:
            raise GuardError("E_JOB_MISMATCH", "wait result is for a different host job")
        self.store.transition("wait_result", result=result, lease_id=lease_id, expected_lease_generation=lease["generation"], timestamp=self._now())
        if result.get("wakeup_required") or (isinstance(result.get("progress"), dict) and result["progress"].get("forced_suspension")):
            return self.store.transition("wakeup_needed", reason="host forced suspension", timestamp=self._now())
        if result["status"] == "unknown":
            return self.state()
        if result["status"] in {"queued", "running"}:
            return self.heartbeat(lease_id=lease_id)
        if result["user_attention_required"]:
            return self.store.transition("block", reason="host requires user input", job_id=result["host_job_id"], host_status=result["status"], expected_lease_generation=lease["generation"])
        if result["status"] in {"failed", "cancelled"}:
            return self.store.transition("worker_failed", reason=result.get("completion_reason", result["status"]), host_job_id=result["host_job_id"], expected_lease_generation=lease["generation"])
        outcome = None
        if isinstance(result.get("progress"), dict):
            outcome = result["progress"].get("outcome")
        return self.store.transition("worker_complete", host_job_id=result["host_job_id"], receipt_id=result.get("receipt_reference"), outcome=outcome or "success", expected_lease_generation=lease["generation"])

    @staticmethod
    def _wakeup_key(state: Mapping[str, Any], lease: Mapping[str, Any]) -> str:
        job_id = ((state.get("active") or {}).get("job_id") or lease.get("active_job_id") or "")
        cursor = lease.get("wait_cursor") or ""
        return f"{state.get('task_id')}:{state.get('milestone_id')}:{job_id}:{cursor}:{lease.get('generation')}"

    @staticmethod
    def _validate_reconciliation_identity(result: Mapping[str, Any], intent: Mapping[str, Any]) -> None:
        for field in ("invocation_id", "idempotency_key"):
            supplied = result.get(field)
            if supplied is not None and supplied != intent.get(field):
                raise GuardError("E_RECONCILIATION_ID", f"reconciliation {field} does not match the persisted dispatch intent")

    def _record_unknown_reconciliation(self, lease: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        attempts = int(self.state().get("reconciliation_attempts", 0)) + 1
        if attempts >= self.max_reconciliation_attempts:
            return self.store.transition("reconciliation_exhausted", reason="host job could not be resolved within the reconciliation budget", expected_lease_generation=lease["generation"], timestamp=self._now())
        return self.store.transition("reconciliation_needed", result=dict(result), expected_lease_generation=lease["generation"], timestamp=self._now())

    def record_wakeup(self, *, lease_id: str, wakeup: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        required = ("wakeup_id", "thread_id", "task_id", "milestone_id", "state_generation", "lease_id", "active_job_id", "wait_cursor", "earliest_poll_at", "hard_deadline", "resume_instruction", "idempotency_key")
        missing = [key for key in required if key not in wakeup]
        if missing:
            raise GuardError("E_WAKEUP_SCHEMA", f"missing wakeup field(s): {', '.join(missing)}")
        if wakeup["lease_id"] != lease_id:
            raise GuardError("E_WAKEUP_OWNER", "wakeup is bound to a different lease")
        state = self.state()
        active = state.get("active") or {}
        expected_job = active.get("job_id") or lease.get("active_job_id")
        expected_cursor = lease.get("wait_cursor")
        if wakeup["thread_id"] != lease.get("host_thread_id") or wakeup["task_id"] != state.get("task_id") or wakeup["milestone_id"] != state.get("milestone_id"):
            raise GuardError("E_WAKEUP_BINDING", "wakeup task, milestone, or thread does not match persisted state")
        if wakeup["active_job_id"] != expected_job:
            raise GuardError("E_WAKEUP_BINDING", "wakeup job does not match the persisted active job")
        if wakeup["wait_cursor"] != expected_cursor:
            raise GuardError("E_WAKEUP_BINDING", "wakeup cursor does not match the persisted wait cursor")
        if wakeup["state_generation"] != state.get("lease_generation") or wakeup["state_generation"] != lease.get("generation"):
            raise GuardError("E_WAKEUP_GENERATION", "wakeup is bound to a stale state generation")
        expected_key = self._wakeup_key(state, lease)
        if wakeup["idempotency_key"] != expected_key:
            raise GuardError("E_WAKEUP_IDEMPOTENCY", "wakeup idempotency key does not match the persisted job")
        if not isinstance(wakeup["resume_instruction"], str) or "reconcile" not in wakeup["resume_instruction"].lower():
            raise GuardError("E_WAKEUP_RESUME", "wakeup resume instruction must reconcile the persisted job")
        earliest, deadline = _parse_time(wakeup["earliest_poll_at"]), _parse_time(wakeup["hard_deadline"])
        if earliest > deadline:
            raise GuardError("E_WAKEUP_DEADLINE", "wakeup earliest poll must precede its hard deadline")
        if state.get("wakeup") and state["wakeup"].get("wakeup_id") == wakeup["wakeup_id"] and digest(state["wakeup"]) == digest(wakeup):
            return state
        return self.store.transition("wakeup_registered", wakeup=dict(wakeup), wakeup_id=wakeup["wakeup_id"], expected_lease_generation=lease["generation"], timestamp=self._now())

    def clear_wakeup(self, *, lease_id: str) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        return self.store.transition("wakeup_cleared", lease_id=lease_id, expected_lease_generation=lease["generation"], timestamp=self._now())

    def resume_wakeup(self, *, lease_id: str, wakeup_id: str, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Resume the same task from a registered callback and reconcile first."""
        lease = self._check_lease(lease_id)
        wakeup = self.state().get("wakeup") or {}
        if wakeup.get("wakeup_id") != wakeup_id:
            raise GuardError("E_WAKEUP_IDENTITY", "resume callback does not match the registered wakeup")
        self.clear_wakeup(lease_id=lease_id)
        if result is not None:
            return self.reconcile(lease_id=lease_id, result=result)
        return self.next_action()

    def reconcile(self, *, lease_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        result = validate_host_result(result)
        state = self.state()
        pending = state.get("pending_dispatch")
        if pending:
            self._validate_reconciliation_identity(result, pending)
            if result["status"] == "unknown":
                return self._record_unknown_reconciliation(lease, result)
            if lease.get("status") == "handoff_pending":
                self.store.transition("lease_handoff_adopted", lease_id=lease_id, generation=lease["generation"], expected_lease_generation=lease["generation"], expires_at=_timestamp(self.clock() + self.lease_duration_seconds), timestamp=self._now())
            return self.record_dispatch_result(lease_id=lease_id, result=result)
        if lease.get("status") == "handoff_pending" or state.get("reconciliation_required"):
            if result["status"] == "unknown":
                return self._record_unknown_reconciliation(lease, result)
            if lease.get("status") == "handoff_pending" and (state.get("active") or {}).get("job_id") != result["host_job_id"]:
                raise GuardError("E_JOB_MISMATCH", "handoff result is for a different host job")
            if lease.get("status") == "handoff_pending":
                self.store.transition("lease_handoff_adopted", lease_id=lease_id, generation=lease["generation"], expected_lease_generation=lease["generation"], expires_at=_timestamp(self.clock() + self.lease_duration_seconds), timestamp=self._now())
            if state.get("reconciliation_required"):
                self.store.transition("reconciliation_resolved", expected_lease_generation=lease["generation"], timestamp=self._now())
        return self.record_wait_result(lease_id=lease_id, result=result)

    def record_receipt(self, *, lease_id: str, reference: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        state = self.state()
        result = state.get("last_host_result") or {}
        host_job_id = _require_string(result.get("host_job_id"), "host_job_id")
        expected_reference = _require_string(result.get("receipt_reference"), "receipt_reference")
        if reference != expected_reference:
            raise GuardError("E_RECEIPT_BINDING", "receipt reference does not match the completed host result")
        trusted = _trusted_receipt(receipt, state, host_job_id=host_job_id, reference=reference)
        receipt_id = trusted["invocation_id"]
        receipt_digest = digest(trusted)
        pending = self.state().get("pending_receipt") or {}
        if pending.get("receipt_id") == receipt_id and pending.get("receipt_digest") == receipt_digest:
            return self.state()
        intent = state.get("last_dispatch_intent") or {}
        return self.store.transition("receipt_recorded", receipt_id=receipt_id, receipt_digest=receipt_digest, reference=reference, host_job_id=host_job_id, idempotency_key=trusted["idempotency_key"], outcome=trusted["outcome"], finding_ids=trusted["finding_ids"], expected_lease_generation=lease["generation"], timestamp=self._now())

    def validate_receipt(self, *, lease_id: str, outcome: str, receipt_id: str | None = None, receipt_digest: str | None = None, finding_ids: list[str] | None = None) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        state = self.state(); pending = state.get("pending_receipt") or {}
        receipt_id = receipt_id or pending.get("receipt_id") or state.get("last_receipt")
        if not receipt_id:
            raise GuardError("E_RECEIPT_MISSING", "no receipt is available to validate")
        if receipt_id in state.get("consumed_receipts", {}):
            raise GuardError("E_RECEIPT_DUPLICATE", "receipt has already been consumed")
        expected_digest = pending.get("receipt_digest", "")
        if receipt_digest is not None and receipt_digest != expected_digest:
            raise GuardError("E_RECEIPT_REPLAY", "receipt digest does not match the persisted receipt")
        if outcome != pending.get("outcome"):
            raise GuardError("E_RECEIPT_OUTCOME", "routing outcome does not match the trusted receipt")
        expected_findings = pending.get("finding_ids", [])
        if finding_ids is not None and finding_ids != expected_findings:
            raise GuardError("E_RECEIPT_FINDINGS", "routing findings do not match the trusted receipt")
        return self.store.transition("receipt_validated", outcome=pending["outcome"], receipt_id=receipt_id, receipt_digest=expected_digest, finding_ids=expected_findings, expected_lease_generation=lease["generation"])

    def record_verification(self, *, lease_id: str, passed: bool, reason: str = "") -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        return self.store.transition("verification_passed" if passed else "verification_failed", reason=reason, expected_lease_generation=lease["generation"], timestamp=self._now())

    def record_terra_decision(self, *, lease_id: str, accepted: bool, finding_ids: list[str] | None = None, reason: str = "", complete: bool = False, next_milestone_id: str | None = None) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        state = self.store.transition("terra_accepted" if accepted else "terra_rejected", finding_ids=finding_ids or [], reason=reason, complete=complete, expected_lease_generation=lease["generation"], timestamp=self._now())
        if accepted and not complete and next_milestone_id:
            state = self.store.transition("advance_milestone", milestone_id=next_milestone_id, expected_lease_generation=lease["generation"], timestamp=self._now())
            state = self.store.transition("start_next_milestone", expected_lease_generation=lease["generation"], timestamp=self._now())
        return state

    def next_action(self) -> dict[str, Any]:
        state = self.state(); lease = state.get("continuation_lease") or {}
        if lease.get("status") == "handoff_pending":
            return self._reconcile_action(state, lease, reason="lease handoff requires host reconciliation")
        if state.get("pending_dispatch"):
            return self._reconcile_action(state, lease)
        if state.get("wakeup_required") and state.get("wakeup") and state["wakeup"].get("status", "active") == "active":
            return {"action": "register_wakeup", "lease_id": lease.get("lease_id"), "wakeup_id": state["wakeup"].get("wakeup_id")}
        if state.get("wakeup_required"):
            return {"action": "register_wakeup", "lease_id": lease.get("lease_id"), "job_id": (state.get("active") or {}).get("job_id"), "reason": "host forced suspension"}
        if state.get("reconciliation_required"):
            return self._reconcile_action(state, lease, reason="host returned unknown status")
        if self._has_live_job(state):
            provider = state["active"].get("provider") or ("claude_code" if state["active"].get("phase") in {"review", "rereview"} else "codex")
            return {"action": "wait_claude" if provider == "claude_code" else "wait_codex", "job_id": state["active"].get("job_id"), "cursor": lease.get("wait_cursor"), "lease_id": lease.get("lease_id"), "timeout_seconds": 60}
        if state.get("receipt_recovery_requested"):
            return {"action": "recover_receipt", "receipt_reference": state.get("last_receipt"), "lease_id": lease.get("lease_id"), "job_id": state.get("last_host_job_id")}
        if state.get("pending_receipt"):
            return {"action": "validate_receipt", "receipt_reference": state["pending_receipt"].get("reference"), "lease_id": lease.get("lease_id")}
        state_name = state["state"]
        if state_name.endswith("_dispatching") or state_name in {"planned", "needs_correction"}:
            worker = state.get("implementer") if state_name in {"planned", "correction_dispatching", "needs_correction"} else state.get("reviewer")
            if state_name == "rereview_dispatching": worker = state.get("reviewer")
            provider = "claude_code" if worker == "claude_worker" else "codex"
            phase = "implementation" if state_name == "planned" else "correction" if state_name in {"needs_correction", "correction_dispatching"} else "rereview" if state_name == "rereview_dispatching" else "review"
            binding = (state.get("runtime_bindings") or {}).get(worker, {})
            action = {"action": "dispatch_claude" if provider == "claude_code" else "dispatch_codex", "provider": provider, "role": worker, "phase": phase, "task_id": state["task_id"], "milestone_id": state["milestone_id"], "milestone_index": state.get("milestone_index", 1), "round": state.get("round", 0), "state_generation": state.get("lease_generation", 0), "lease_id": lease.get("lease_id"), "role_instance_id": binding.get("role_instance_id"), "session_id": binding.get("session_id"), "context_id": binding.get("context_id"), "host_id": binding.get("host_id"), "resolved_profile": binding.get("resolved_profile"), "resolved_orchestrator_profile": state.get("resolved_orchestrator_profile"), "policy_digest": state.get("policy_digest"), "worktree": state.get("worktree", "/"), "path_scope": state.get("path_scope", ["." ]), "prompt_artifact": state.get("prompt_artifact", f"{state['task_id']}-{phase}"), "invocation_id": f"{state['task_id']}-{state['milestone_id']}-{phase}-{state.get('invocation_count', 0) + 1}", "idempotency_key": f"{state['task_id']}:{state['milestone_id']}:{phase}:{state.get('invocation_count', 0) + 1}", "timeout_seconds": state.get("timeout_seconds", 3600 if phase in {"implementation", "correction"} else 2400), "receipt_location": state.get("receipt_location", "worker-receipt.json")}
            action = {key: value for key, value in action.items() if value is not None}
            return validate_host_action(action)
        if state_name.endswith("_validating"):
            return {"action": "validate_receipt", "receipt_reference": (state.get("last_host_result") or {}).get("receipt_reference"), "job_id": state.get("last_host_job_id"), "lease_id": lease.get("lease_id")}
        if state_name == "milestone_verifying": return {"action": "run_verification", "lease_id": lease.get("lease_id")}
        if state_name == "awaiting_terra_decision": return {"action": "request_terra_decision", "lease_id": lease.get("lease_id")}
        if state_name == "advancing_milestone": return {"action": "reconcile_job", "reason": "start_next_milestone", "lease_id": lease.get("lease_id")}
        if state_name == "milestone_complete": return {"action": "request_terra_decision", "lease_id": lease.get("lease_id"), "reason": "complete-or-advance"}
        if state_name in TERMINAL_STATES and not self._has_live_job(state) and not state.get("pending_dispatch") and not state.get("pending_receipt") and not state.get("pending_terra_decision") and not state.get("wakeup"):
            return {"action": "final_allowed", "lease_id": lease.get("lease_id")}
        return {"action": "reconcile_job", "lease_id": lease.get("lease_id")}

    @staticmethod
    def _reconcile_action(state: Mapping[str, Any], lease: Mapping[str, Any], *, reason: str = "pending dispatch intent") -> dict[str, Any]:
        intent = state.get("pending_dispatch") or state.get("last_dispatch_intent") or {}
        return {"action": "reconcile_job", "lease_id": lease.get("lease_id"), "idempotency_key": intent.get("idempotency_key"), "job_id": (state.get("active") or {}).get("job_id") or lease.get("active_job_id"), "cursor": lease.get("wait_cursor"), "timeout_seconds": 60, "reason": reason}

    def _finalizable(self, state: Mapping[str, Any]) -> bool:
        lease = state.get("continuation_lease") or {}
        return state.get("state") in TERMINAL_STATES and state.get("next_action") is None and not self._has_live_job(state) and not state.get("pending_dispatch") and not state.get("pending_receipt") and not state.get("pending_terra_decision") and not state.get("wakeup") and lease.get("status") == "released"

    @staticmethod
    def _has_live_job(state: Mapping[str, Any]) -> bool:
        active = state.get("active")
        return bool(active and active.get("status", "running") not in {"completed", "needs_attention", "failed", "cancelled"})

    def assert_finalizable(self, *, lease_id: str | None = None) -> dict[str, Any]:
        state = self.state()
        # Releasing the owned lease here makes finalization a single guarded
        # terminal transaction from the caller's perspective.
        lease = state.get("continuation_lease") or {}
        if state.get("state") in TERMINAL_STATES and not self._has_live_job(state) and not state.get("pending_dispatch") and not state.get("pending_receipt") and not state.get("pending_terra_decision") and not state.get("wakeup") and lease.get("status") in {"active", "handoff_pending"}:
            self.release_lease(lease_id=lease_id or lease.get("lease_id"))
            state = self.state()
        allowed = self._finalizable(state)
        reason = "finalization permitted" if allowed else f"state={state.get('state')}, unresolved host side effect or lease"
        event_type = "finalization_checked" if allowed else "premature_final_attempt"
        self.store.transition(event_type, check_id=os.urandom(8).hex(), allowed=allowed, reason=reason, timestamp=self._now())
        if not allowed:
            raise GuardError("E_FINAL_NOT_ALLOWED", reason)
        return {"action": "final_allowed", "state": state["state"]}

    def release_lease(self, *, lease_id: str) -> dict[str, Any]:
        lease = self._check_lease(lease_id)
        state = self.state()
        if self._has_live_job(state) or state.get("pending_dispatch") or state.get("pending_receipt") or state.get("pending_terra_decision") or state.get("wakeup"):
            raise GuardError("E_LEASE_RELEASE", "cannot release lease with unresolved host work")
        return self.store.transition("lease_released", lease_id=lease_id, expected_lease_generation=lease["generation"], timestamp=self._now())


class ContinuationSupervisor:
    """Drive guard actions in one run using a supplied host adapter."""

    def __init__(self, guard: ContinuationGuard, adapter: Any, *, lease_id: str, owner_role_instance_id: str = "luna-orchestrator", host_thread_id: str = "thread", max_steps: int = 1000):
        self.guard, self.adapter, self.lease_id = guard, adapter, lease_id
        self.owner_role_instance_id, self.host_thread_id, self.max_steps = owner_role_instance_id, host_thread_id, max_steps

    def run(self) -> dict[str, Any]:
        self.guard.acquire_lease(lease_id=self.lease_id, owner_role_instance_id=self.owner_role_instance_id, host_thread_id=self.host_thread_id)
        for _ in range(self.max_steps):
            action = self.guard.next_action()
            kind = action["action"]
            if kind == "final_allowed":
                return self.guard.assert_finalizable(lease_id=self.lease_id)
            if kind in DISPATCH_ACTIONS:
                self.guard.record_dispatch_intent(action)
                result = self.adapter.dispatch(action)
                self.guard.record_dispatch_result(lease_id=self.lease_id, result=result)
            elif kind in {"wait_codex", "wait_claude", "reconcile_job"}:
                if kind == "reconcile_job":
                    if not hasattr(self.adapter, "reconcile"):
                        raise GuardError("E_HOST_RECONCILIATION", "host adapter must implement stable reconcile(job_id, cursor, idempotency_key)")
                    result = self.adapter.reconcile(action)
                else:
                    result = self.adapter.wait(action)
                self.guard.reconcile(lease_id=self.lease_id, result=result)
            elif kind == "validate_receipt":
                try:
                    receipt = self.adapter.receipt(action)
                except Exception as exc:
                    self.guard.store.transition("receipt_recovery_needed", timestamp=self.guard._now(), reason=str(exc))
                    continue
                if receipt is None:
                    self.guard.store.transition("receipt_recovery_needed", timestamp=self.guard._now())
                else:
                    self.guard.record_receipt(lease_id=self.lease_id, reference=action.get("receipt_reference", "receipt"), receipt=receipt)
                    verdict = self.adapter.validate_receipt(receipt, self.guard.state()) if hasattr(self.adapter, "validate_receipt") else {"outcome": receipt.get("outcome", "success"), "finding_ids": receipt.get("finding_ids", [])}
                    self.guard.validate_receipt(lease_id=self.lease_id, **verdict)
            elif kind == "recover_receipt":
                try:
                    receipt = self.adapter.recover_receipt(action)
                except Exception as exc:
                    self.guard.store.transition("receipt_recovery_failed", timestamp=self.guard._now(), reason=str(exc))
                    continue
                self.guard.record_receipt(lease_id=self.lease_id, reference=action.get("receipt_reference", "receipt"), receipt=receipt)
            elif kind == "run_verification":
                result = self.adapter.verify(self.guard.state())
                self.guard.record_verification(lease_id=self.lease_id, passed=bool(result.get("passed")), reason=result.get("reason", ""))
            elif kind == "request_terra_decision":
                result = self.adapter.terra_decision(self.guard.state())
                self.guard.record_terra_decision(lease_id=self.lease_id, accepted=bool(result.get("accepted")), finding_ids=result.get("finding_ids", []), reason=result.get("reason", ""), complete=bool(result.get("complete", False)), next_milestone_id=result.get("next_milestone_id"))
            elif kind == "register_wakeup":
                wakeup = self.adapter.register_wakeup(action)
                self.guard.record_wakeup(lease_id=self.lease_id, wakeup=wakeup)
            else:
                raise GuardError("E_GUARD_ACTION", f"supervisor cannot execute {kind}")
        raise GuardError("E_SUPERVISOR_STEPS", "supervisor exceeded deterministic step budget")


def _payload(args: argparse.Namespace) -> dict[str, Any]:
    try:
        value = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        raise GuardError("E_GUARD_SCHEMA", f"payload is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardError("E_GUARD_SCHEMA", "payload must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and persist host-native continuation records")
    parser.add_argument("--store", required=True)
    parser.add_argument("--lease-duration-seconds", type=int, default=120)
    parser.add_argument("--max-reconciliation-attempts", type=int, default=3)
    parser.add_argument("--payload", default="{}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire-lease", "heartbeat", "record-dispatch-intent", "record-dispatch-result", "record-wait-result", "record-wakeup", "reconcile", "next-action", "assert-finalizable", "release-lease"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    guard = ContinuationGuard(args.store, lease_duration_seconds=args.lease_duration_seconds, max_reconciliation_attempts=args.max_reconciliation_attempts)
    payload = _payload(args)
    try:
        if args.command == "acquire-lease": result = guard.acquire_lease(lease_id=payload["lease_id"], owner_role_instance_id=payload["owner_role_instance_id"], host_thread_id=payload["host_thread_id"], wait_cursor=payload.get("wait_cursor"))
        elif args.command == "heartbeat": result = guard.heartbeat(lease_id=payload["lease_id"])
        elif args.command == "record-dispatch-intent": result = guard.record_dispatch_intent(payload["action"])
        elif args.command == "record-dispatch-result": result = guard.record_dispatch_result(lease_id=payload["lease_id"], result=payload["result"])
        elif args.command == "record-wait-result": result = guard.record_wait_result(lease_id=payload["lease_id"], result=payload["result"])
        elif args.command == "record-wakeup": result = guard.record_wakeup(lease_id=payload["lease_id"], wakeup=payload["wakeup"])
        elif args.command == "reconcile": result = guard.reconcile(lease_id=payload["lease_id"], result=payload["result"])
        elif args.command == "next-action": result = guard.next_action()
        elif args.command == "assert-finalizable": result = guard.assert_finalizable(lease_id=payload.get("lease_id"))
        else: result = guard.release_lease(lease_id=payload["lease_id"])
        print(json.dumps(result, sort_keys=True, indent=2)); return 0
    except (GuardError, StateError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"continuation_guard: {exc}", file=os.sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
