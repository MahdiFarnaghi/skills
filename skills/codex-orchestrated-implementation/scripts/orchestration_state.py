#!/usr/bin/env python3
"""Durable, host-agnostic state machine for continuous Luna orchestration.

This module deliberately does not invoke workers. Luna persists an intent,
uses an approved host-native dispatch/wait mechanism, and then records the
result here. The module answers the deterministic question: what may happen
next?
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import argparse
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

STATES = frozenset({
    "planned", "implementation_dispatching", "implementation_running",
    "implementation_validating", "review_dispatching", "review_running",
    "review_validating", "needs_correction", "correction_dispatching",
    "correction_running", "correction_validating", "rereview_dispatching",
    "rereview_running", "rereview_validating", "milestone_verifying",
    "awaiting_terra_decision", "milestone_complete", "advancing_milestone",
    "complete", "blocked", "escalated", "cancelled",
})
TERMINAL_STATES = frozenset({"complete", "blocked", "escalated", "cancelled"})
DEFAULT_LIMITS = {
    "max_correction_rounds": 20,
    "max_correction_reviews_per_defect": 3,
    "max_invocations": 48,
    "max_failed_invocations": 6,
}
LEASE_STATUSES = frozenset({"active", "handoff_pending", "released", "expired"})


class StateError(ValueError):
    """A fail-closed state or transition error."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_id(event: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(event).encode()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class OrchestrationStore:
    """Append-only event log plus an atomically replaced current projection."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.events_path = self.directory / "events.jsonl"
        self.state_path = self.directory / "state.json"
        self.lock_path = self.directory / ".lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]

    def create(self, *, task_id: str, milestone_id: str, milestone_index: int = 1,
               implementer: str = "luna_worker", reviewer: str = "claude_worker",
               limits: Mapping[str, int] | None = None, **bindings: Any) -> dict[str, Any]:
        if self.read() is not None:
            raise StateError("state already exists")
        selected = dict(DEFAULT_LIMITS)
        selected.update(limits or {})
        if selected["max_invocations"] < 2 * (1 + selected["max_correction_rounds"]) + selected["max_failed_invocations"]:
            raise StateError("max_invocations cannot cover correction rounds and failure budget")
        state = {
            "schema_version": 2, "task_id": task_id, "milestone_id": milestone_id,
            "milestone_index": milestone_index, "state": "planned", "previous_state": None, "round": 0,
            "implementer": implementer, "reviewer": reviewer, "limits": selected,
            "invocation_count": 0, "failed_invocations": 0, "correction_reviews": {},
            "active": None, "last_event_id": None,
            "created_at": _now(), "transition_at": _now(), "dispatch_at": None,
            "heartbeat_at": None, "progress_at": None, "timeout_at": None,
            "continuation_lease": None, "lease_generation": 0,
            "pending_dispatch": None, "pending_receipt": None,
            "last_dispatch_intent": None, "last_host_result": None,
            "reconciliation_required": False, "reconciliation_attempts": 0,
            "receipt_recovery_requested": False, "receipt_recovery_attempts": 0,
            "pending_terra_decision": False, "wakeup": None,
            "consumed_receipts": {}, "audit": [],
            "next_action": {"kind": "dispatch", "worker": implementer, "phase": "implementation"},
            **bindings,
        }
        event = {"type": "created", "task_id": task_id, "milestone_id": milestone_id, "state": state}
        event["event_id"] = _event_id(event)
        with self.locked():
            if self.read() is not None:
                raise StateError("state already exists")
            self._append(event)
            _atomic_json(self.state_path, state)
        return state

    def _append(self, event: Mapping[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def transition(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self.locked():
            state = self.read()
            if state is None:
                raise StateError("state has not been created")
            event = {"type": event_type, "task_id": state["task_id"], "milestone_id": state["milestone_id"], **payload}
            event["event_id"] = _event_id(event)
            if event["event_id"] == state.get("last_event_id"):
                return state
            events = self.events()
            if event["event_id"] in {item["event_id"] for item in events}:
                return state
            _reject_conflicting_reuse(events, event)
            updated = _apply(state, event)
            updated["last_event_id"] = event["event_id"]
            self._append(event)
            _atomic_json(self.state_path, updated)
            return updated

    def reconstruct(self) -> dict[str, Any] | None:
        """Rebuild the projection from the append-only log for restart checks."""
        events = self.events()
        if not events:
            return None
        first = events[0]
        if first.get("type") != "created":
            raise StateError("event log does not start with created")
        projected = dict(first["state"])
        for event in events[1:]:
            projected = _apply(projected, event)
            projected["last_event_id"] = event["event_id"]
        return projected

    # Named operations are deliberately thin wrappers around transition().
    # They make the host-neutral API explicit for Luna callers and keep all
    # mutation under the same append-only/CAS lock.
    def register_dispatch(self, **payload: Any) -> dict[str, Any]:
        return self.transition("dispatch", **payload)

    def record_progress(self, **payload: Any) -> dict[str, Any]:
        return self.transition("heartbeat", **payload)

    def ingest_receipt(self, **payload: Any) -> dict[str, Any]:
        return self.transition("receipt_recorded", **payload)

    def record_validation(self, **payload: Any) -> dict[str, Any]:
        return self.transition("receipt_validated", **payload)

    def mark_failure(self, **payload: Any) -> dict[str, Any]:
        return self.transition("worker_failed", **payload)

    def mark_timeout(self, *, kind: str = "hard", **payload: Any) -> dict[str, Any]:
        payload.setdefault("reason", f"{kind} timeout")
        return self.transition("worker_failed", **payload)

    def enter_terminal(self, status: str, *, reason: str = "") -> dict[str, Any]:
        event = {"complete": "task_complete", "blocked": "block", "escalated": "escalate", "cancelled": "cancel"}.get(status)
        if event is None:
            raise StateError(f"unsupported terminal status {status!r}")
        return self.transition(event, reason=reason)


def _reject_conflicting_reuse(events: list[dict[str, Any]], event: Mapping[str, Any]) -> None:
    """Reject reused identifiers whose canonical event payload changed."""
    if event.get("type") in {"wait_result", "lease_heartbeat", "finalization_checked", "premature_final_attempt"}:
        return
    identifiers = ("invocation_id", "job_id", "host_job_id", "receipt_id", "receipt_digest", "wakeup_id")
    for key in identifiers:
        value = event.get(key)
        if value in (None, ""):
            continue
        for prior in events:
            if prior.get(key) == value and prior.get("type") == event.get("type") and _canonical(prior) != _canonical(event):
                raise StateError(f"{key} {value!r} is being reused with conflicting content")


def _dispatch_action(phase: str, state: Mapping[str, Any]) -> dict[str, Any]:
    worker = state["implementer"] if phase in {"implementation", "correction"} else state["reviewer"]
    if phase == "correction":
        worker = state["implementer"]
    return {"kind": "dispatch", "phase": phase, "worker": worker, "round": state["round"]}


def _apply(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(state)
    current["correction_reviews"] = dict(state.get("correction_reviews", {}))
    current["limits"] = dict(state["limits"])
    current["consumed_receipts"] = dict(state.get("consumed_receipts", {}))
    current["audit"] = list(state.get("audit", []))
    current.setdefault("schema_version", 2)
    current.setdefault("previous_state", None)
    current.setdefault("continuation_lease", None)
    current.setdefault("lease_generation", 0)
    current.setdefault("pending_dispatch", None)
    current.setdefault("pending_receipt", None)
    current.setdefault("last_dispatch_intent", None)
    current.setdefault("last_host_result", None)
    current.setdefault("reconciliation_required", False)
    current.setdefault("reconciliation_attempts", 0)
    current.setdefault("receipt_recovery_requested", False)
    current.setdefault("receipt_recovery_attempts", 0)
    current.setdefault("pending_terra_decision", False)
    current.setdefault("wakeup", None)
    current.setdefault("wakeup_required", False)
    current.setdefault("created_at", _now())
    current.setdefault("transition_at", _now())
    current.setdefault("dispatch_at", None)
    current.setdefault("heartbeat_at", None)
    current.setdefault("progress_at", None)
    current.setdefault("timeout_at", None)
    old = state["state"]
    event_type = event["type"]
    expected_generation = event.get("expected_lease_generation")
    if expected_generation is not None and int(state.get("lease_generation", 0)) != expected_generation:
        raise StateError("stale continuation lease generation")
    current["previous_state"] = old
    current["transition_at"] = event.get("timestamp") or _now()
    if old in TERMINAL_STATES:
        if event_type in {"finalization_checked", "premature_final_attempt", "lease_released"}:
            pass
        else:
            raise StateError(f"terminal state {old} cannot transition")

    if event_type == "lease_acquired":
        existing = state.get("continuation_lease")
        now = event.get("timestamp") or _now()
        if existing and existing.get("status") in {"active", "handoff_pending"} and existing.get("expires_at", "") > now and existing.get("lease_id") != event.get("lease_id"):
            raise StateError("continuation lease is owned by another live Luna instance")
        current["lease_generation"] = int(state.get("lease_generation", 0)) + 1
        current["continuation_lease"] = {
            "lease_id": event["lease_id"], "owner_role_instance_id": event["owner_role_instance_id"],
            "host_thread_id": event["host_thread_id"], "acquired_at": now, "heartbeat_at": now,
            "expires_at": event["expires_at"], "active_job_id": (state.get("active") or {}).get("job_id"),
            "active_job_kind": (state.get("active") or {}).get("provider"), "wait_cursor": event.get("wait_cursor"),
            "wakeup_id": (state.get("wakeup") or {}).get("wakeup_id"),
            "status": "handoff_pending" if state.get("active") else "active",
            "generation": current["lease_generation"],
        }
    elif event_type == "lease_handoff_adopted":
        lease = dict(state.get("continuation_lease") or {})
        if lease.get("lease_id") != event.get("lease_id") or lease.get("status") != "handoff_pending":
            raise StateError("lease handoff is not owned by the adopting continuation")
        if event.get("generation") != lease.get("generation"):
            raise StateError("stale continuation lease generation")
        lease["status"] = "active"
        lease["heartbeat_at"] = event.get("timestamp") or _now()
        lease["expires_at"] = event["expires_at"]
        current["continuation_lease"] = lease
    elif event_type == "lease_heartbeat":
        lease = dict(state.get("continuation_lease") or {})
        if lease.get("lease_id") != event.get("lease_id"):
            raise StateError("heartbeat is not from the lease owner")
        lease.update({"heartbeat_at": event.get("timestamp") or _now(), "expires_at": event["expires_at"], "status": "active", "generation": event.get("generation", state.get("lease_generation", 0))})
        current["continuation_lease"] = lease
        current["reconciliation_required"] = False
    elif event_type == "lease_released":
        lease = dict(state.get("continuation_lease") or {})
        if lease and event.get("lease_id") not in {None, lease.get("lease_id")}:
            raise StateError("cannot release a different continuation lease")
        if lease:
            lease["status"] = "released"
            lease["heartbeat_at"] = event.get("timestamp") or _now()
            lease["active_job_id"] = None
            lease["wakeup_id"] = None
        current["continuation_lease"] = lease or None
    elif event_type == "dispatch_intent":
        if state.get("pending_dispatch") is not None:
            if state["pending_dispatch"] != event.get("intent"):
                raise StateError("a different dispatch intent is already pending")
        else:
            current["pending_dispatch"] = event.get("intent")
            if old == "planned":
                current["state"] = "implementation_dispatching"
    elif event_type == "dispatch_result":
        result = event.get("result") or {}
        current["last_dispatch_intent"] = dict(event.get("intent") or state.get("pending_dispatch") or {})
        current["last_host_result"] = dict(result)
        if result.get("status") == "unknown":
            # An unknown response is not evidence that dispatch succeeded.
            current["pending_dispatch"] = dict(event.get("intent") or state.get("pending_dispatch") or {})
            current["reconciliation_required"] = True
        else:
            current["pending_dispatch"] = None
            current["reconciliation_required"] = False
        current["host_result"] = dict(result)
        if result.get("status") in {"failed", "cancelled"}:
            current["next_action"] = {"kind": "reconcile"}
    elif event_type == "wait_result":
        result = event.get("result") or {}
        current["last_host_result"] = dict(result)
        current["reconciliation_required"] = result.get("status") == "unknown"
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"])
            lease["wait_cursor"] = result.get("cursor")
            lease["heartbeat_at"] = event.get("timestamp") or _now()
            lease["active_job_id"] = result.get("host_job_id") or lease.get("active_job_id")
            current["continuation_lease"] = lease
        current["last_progress"] = result.get("progress")
    elif event_type == "reconciliation_needed":
        current["reconciliation_required"] = True
        current["reconciliation_attempts"] = int(state.get("reconciliation_attempts", 0)) + 1
        current["last_host_result"] = dict(event.get("result") or {})
    elif event_type == "reconciliation_resolved":
        current["reconciliation_required"] = False
        current["reconciliation_attempts"] = 0
    elif event_type == "reconciliation_exhausted":
        current["reconciliation_required"] = False
        current["pending_dispatch"] = None
        current["state"] = "escalated"
        current["terminal_reason"] = event.get("reason", "host job reconciliation budget exhausted")
    elif event_type == "wakeup_registered":
        wakeup = dict(event.get("wakeup") or {})
        if current.get("wakeup"):
            if current["wakeup"].get("wakeup_id") != wakeup.get("wakeup_id"):
                raise StateError("a different wakeup is already registered")
            wakeup = current["wakeup"]
        current["wakeup"] = wakeup
        current["wakeup_required"] = False
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"]); lease["wakeup_id"] = wakeup.get("wakeup_id"); current["continuation_lease"] = lease
    elif event_type == "wakeup_cleared":
        current["wakeup"] = None
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"]); lease["wakeup_id"] = None; current["continuation_lease"] = lease
    elif event_type == "receipt_recorded":
        receipt_id = event.get("receipt_id")
        digest = event.get("receipt_digest")
        if current["consumed_receipts"].get(receipt_id) not in {None, digest}:
            raise StateError("receipt id is reused with a different digest")
        current["pending_receipt"] = {
            "receipt_id": receipt_id, "receipt_digest": digest,
            "reference": event.get("reference"), "host_job_id": event.get("host_job_id"),
            "idempotency_key": event.get("idempotency_key"),
            "outcome": event.get("outcome"), "finding_ids": list(event.get("finding_ids", [])),
            "consumed": False,
        }
        current["receipt_recovery_requested"] = False
    elif event_type == "receipt_recovery_needed":
        current["receipt_recovery_requested"] = True
        current["receipt_recovery_attempts"] = int(state.get("receipt_recovery_attempts", 0)) + 1
    elif event_type == "receipt_recovery_failed":
        current["receipt_recovery_requested"] = False
        current["state"] = "escalated"
        current["terminal_reason"] = event.get("reason", "receipt recovery failed")
    elif event_type == "receipt_consumed":
        receipt_id = event.get("receipt_id")
        digest = event.get("receipt_digest")
        if current["consumed_receipts"].get(receipt_id) not in {None, digest}:
            raise StateError("receipt digest conflicts with a prior consumption")
        current["consumed_receipts"][receipt_id] = digest
        current["pending_receipt"] = None
        current["receipt_recovery_requested"] = False
    elif event_type in {"finalization_checked", "premature_final_attempt"}:
        current["audit"].append({"type": event_type, "timestamp": event.get("timestamp") or _now(), "allowed": event.get("allowed", False), "reason": event.get("reason")})

    dispatch_states = {
        "planned": ("implementation_dispatching", "implementation"),
        "review_dispatching": ("review_dispatching", "review"),
        "needs_correction": ("correction_dispatching", "correction"),
        "correction_dispatching": ("correction_dispatching", "correction"),
        "rereview_dispatching": ("rereview_dispatching", "rereview"),
    }
    if event_type == "dispatch":
        expected = dispatch_states.get(old)
        if old == "implementation_dispatching":
            expected = ("implementation_dispatching", "implementation")
        if expected is None or event.get("phase") != expected[1]:
            raise StateError(f"dispatch is invalid from {old}")
        current["state"] = {"implementation": "implementation_running", "review": "review_running", "correction": "correction_running", "rereview": "rereview_running"}[expected[1]]
        current["active"] = {"job_id": event.get("job_id"), "invocation_id": event.get("invocation_id"), "phase": expected[1], "provider": event.get("provider"), "role_instance_id": event.get("role_instance_id"), "session_id": event.get("session_id"), "context_id": event.get("context_id"), "host_id": event.get("host_id"), "status": "running"}
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"])
            lease["active_job_id"] = event.get("job_id")
            provider = event.get("provider")
            lease["active_job_kind"] = "claude" if provider == "claude_code" or expected[1] in {"review", "rereview"} else "codex"
            current["continuation_lease"] = lease
        current["invocation_count"] += 1
        current["dispatch_at"] = current["transition_at"]
        if current["invocation_count"] > current["limits"]["max_invocations"]:
            raise StateError("invocation limit exceeded")
    elif event_type == "heartbeat" and old.endswith("_running"):
        current["last_heartbeat"] = event.get("timestamp")
        current["heartbeat_at"] = current["transition_at"]
        current["progress_at"] = current["transition_at"]
    elif event_type == "worker_complete" and old.endswith("_running"):
        phase = current["active"]["phase"]
        current["state"] = {"implementation": "implementation_validating", "review": "review_validating", "correction": "correction_validating", "rereview": "rereview_validating"}[phase]
        current["active"] = None
        current["last_receipt"] = event.get("receipt_id")
        current["worker_outcome"] = event.get("outcome")
        current["last_host_job_id"] = event.get("host_job_id") or (state.get("active") or {}).get("job_id")
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"]); lease["active_job_id"] = None; lease["active_job_kind"] = None; current["continuation_lease"] = lease
    elif event_type == "worker_failed" and old.endswith("_running"):
        current["failed_invocations"] += 1
        current["timeout_at"] = current["transition_at"] if "timeout" in str(event.get("reason", "")).lower() else current.get("timeout_at")
        phase = current["active"]["phase"]
        current["active"] = None
        if current.get("continuation_lease"):
            lease = dict(current["continuation_lease"]); lease["active_job_id"] = None; lease["active_job_kind"] = None; current["continuation_lease"] = lease
        if current["failed_invocations"] > current["limits"]["max_failed_invocations"]:
            current["state"] = "escalated"; current["terminal_reason"] = "failed invocation limit exceeded"
        else:
            current["state"] = {"implementation": "planned", "review": "review_dispatching", "correction": "correction_dispatching", "rereview": "rereview_dispatching"}[phase]
    elif event_type == "dispatch_failed" and (old.endswith("_dispatching") or old == "planned"):
        current["invocation_count"] += 1
        current["failed_invocations"] += 1
        if current["invocation_count"] > current["limits"]["max_invocations"] or current["failed_invocations"] > current["limits"]["max_failed_invocations"]:
            current["state"] = "escalated"; current["terminal_reason"] = "worker dispatch failure limit exceeded"
        else:
            phase = {"planned": "planned", "implementation_dispatching": "planned", "review_dispatching": "review_dispatching", "correction_dispatching": "correction_dispatching", "rereview_dispatching": "rereview_dispatching"}[old]
            current["state"] = phase
    elif event_type == "receipt_validated":
        receipt_id = event.get("receipt_id") or state.get("last_receipt")
        if receipt_id and receipt_id in current["consumed_receipts"]:
            raise StateError("receipt has already been consumed")
        if receipt_id:
            current["consumed_receipts"][receipt_id] = event.get("receipt_digest", "")
        current["pending_receipt"] = None
        if old == "implementation_validating":
            current["state"] = "review_dispatching"
        elif old in {"review_validating", "rereview_validating"}:
            outcome = event.get("outcome")
            if outcome == "approve":
                current["state"] = "milestone_verifying"
            elif outcome == "needs_correction":
                for finding in event.get("finding_ids", []):
                    current["correction_reviews"][finding] = current["correction_reviews"].get(finding, 0) + 1
                    if current["correction_reviews"][finding] > current["limits"]["max_correction_reviews_per_defect"]:
                        current["state"] = "escalated"; current["terminal_reason"] = f"correction review limit exceeded for {finding}"; break
                else:
                    current["state"] = "needs_correction"
        elif old == "correction_validating":
            current["round"] += 1
            if current["round"] > current["limits"]["max_correction_rounds"]:
                current["state"] = "escalated"; current["terminal_reason"] = "correction round limit exceeded"
            else:
                current["state"] = "rereview_dispatching"
        else:
            raise StateError(f"receipt validation is invalid from {old}")
    elif event_type == "verification_passed" and old == "milestone_verifying":
        current["state"] = "awaiting_terra_decision"
    elif event_type == "terra_accepted" and old == "awaiting_terra_decision":
        current["state"] = "complete" if event.get("complete") else "milestone_complete"
        current["pending_terra_decision"] = False
    elif event_type == "terra_rejected" and old == "awaiting_terra_decision":
        current["pending_terra_decision"] = False
        current["state"] = "needs_correction" if event.get("finding_ids") else "escalated"
        if current["state"] == "escalated": current["terminal_reason"] = event.get("reason", "Terra rejected milestone")
    elif event_type == "verification_failed" and old == "milestone_verifying":
        current["state"] = "escalated"; current["terminal_reason"] = event.get("reason", "verification failed")
    elif event_type == "terra_requested" and old == "awaiting_terra_decision":
        current["pending_terra_decision"] = True
    elif event_type == "wakeup_needed":
        current["wakeup_required"] = True
    elif event_type == "task_complete" and old == "milestone_complete":
        current["state"] = "complete"
    elif event_type == "advance_milestone" and old == "milestone_complete":
        current["milestone_index"] += 1
        current["milestone_id"] = str(event["milestone_id"])
        current["implementer"], current["reviewer"] = current["reviewer"], current["implementer"]
        current["round"] = 0; current["invocation_count"] = 0; current["failed_invocations"] = 0; current["correction_reviews"] = {}
        current["state"] = "advancing_milestone"
    elif event_type == "start_next_milestone" and old == "advancing_milestone":
        current["state"] = "planned"
    elif event_type in {"lease_acquired", "lease_handoff_adopted", "lease_heartbeat", "lease_released", "dispatch_intent", "dispatch_result", "wait_result", "reconciliation_needed", "reconciliation_resolved", "reconciliation_exhausted", "wakeup_registered", "wakeup_cleared", "receipt_recorded", "receipt_consumed", "receipt_recovery_needed", "receipt_recovery_failed", "finalization_checked", "premature_final_attempt", "wakeup_needed"}:
        pass
    elif event_type in {"block", "escalate", "cancel"}:
        current["state"] = {"block": "blocked", "escalate": "escalated", "cancel": "cancelled"}[event_type]
        current["terminal_reason"] = event.get("reason", event_type)
        if current.get("active") and event.get("host_status"):
            current["active"] = {**current["active"], "status": event["host_status"]}
    else:
        raise StateError(f"event {event_type!r} is invalid from {old!r}")
    current["next_action"] = next_action(current)
    return current


def next_action(state: Mapping[str, Any]) -> dict[str, Any] | None:
    state_name = state["state"]
    if state_name in TERMINAL_STATES:
        return None
    if state_name == "planned": return _dispatch_action("implementation", state)
    if state_name == "review_dispatching": return _dispatch_action("review", state)
    if state_name == "needs_correction": return _dispatch_action("correction", state)
    if state_name == "correction_dispatching": return _dispatch_action("correction", state)
    if state_name == "rereview_dispatching": return _dispatch_action("rereview", state)
    if state_name.endswith("_running"): return {"kind": "wait", "job_id": (state.get("active") or {}).get("job_id")}
    if state_name.endswith("_validating"): return {"kind": "validate_receipt"}
    if state_name == "milestone_verifying": return {"kind": "run_verification"}
    if state_name == "awaiting_terra_decision": return {"kind": "await_terra"}
    if state_name == "milestone_complete": return {"kind": "advance_or_complete"}
    if state_name == "advancing_milestone": return {"kind": "start_next_milestone"}
    return {"kind": "reconcile"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persist and advance Luna orchestration state")
    parser.add_argument("--store", required=True, help="durable state directory")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--task-id", required=True)
    create.add_argument("--milestone-id", required=True)
    create.add_argument("--milestone-index", type=int, default=1)
    create.add_argument("--implementer", default="luna_worker")
    create.add_argument("--reviewer", default="claude_worker")
    transition = sub.add_parser("transition")
    transition.add_argument("event")
    transition.add_argument("--payload", default="{}", help="JSON object of event fields")
    sub.add_parser("state")
    sub.add_parser("next-action")
    args = parser.parse_args(argv)
    store = OrchestrationStore(args.store)
    try:
        if args.command == "create":
            result = store.create(task_id=args.task_id, milestone_id=args.milestone_id, milestone_index=args.milestone_index, implementer=args.implementer, reviewer=args.reviewer)
        elif args.command == "transition":
            payload = json.loads(args.payload)
            if not isinstance(payload, dict):
                raise StateError("transition payload must be a JSON object")
            result = store.transition(args.event, **payload)
        else:
            result = store.read() if args.command == "state" else next_action(store.read() or {})
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, StateError, KeyError) as exc:
        print(f"orchestration_state: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
