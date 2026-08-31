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


class StateError(ValueError):
    """A fail-closed state or transition error."""


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
            "schema_version": 1, "task_id": task_id, "milestone_id": milestone_id,
            "milestone_index": milestone_index, "state": "planned", "round": 0,
            "implementer": implementer, "reviewer": reviewer, "limits": selected,
            "invocation_count": 0, "failed_invocations": 0, "correction_reviews": {},
            "active": None, "last_event_id": None, "next_action": {"kind": "dispatch", "worker": implementer, "phase": "implementation"},
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
            if event["event_id"] in {item["event_id"] for item in self.events()}:
                return state
            updated = _apply(state, event)
            updated["last_event_id"] = event["event_id"]
            self._append(event)
            _atomic_json(self.state_path, updated)
            return updated


def _dispatch_action(phase: str, state: Mapping[str, Any]) -> dict[str, Any]:
    worker = state["implementer"] if phase in {"implementation", "correction"} else state["reviewer"]
    if phase == "correction":
        worker = state["implementer"]
    return {"kind": "dispatch", "phase": phase, "worker": worker, "round": state["round"]}


def _apply(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(state)
    current["correction_reviews"] = dict(state.get("correction_reviews", {}))
    current["limits"] = dict(state["limits"])
    old = state["state"]
    event_type = event["type"]
    if old in TERMINAL_STATES:
        raise StateError(f"terminal state {old} cannot transition")

    dispatch_states = {
        "planned": ("implementation_dispatching", "implementation"),
        "review_dispatching": ("review_dispatching", "review"),
        "needs_correction": ("correction_dispatching", "correction"),
        "correction_dispatching": ("correction_dispatching", "correction"),
        "rereview_dispatching": ("rereview_dispatching", "rereview"),
    }
    if event_type == "dispatch":
        expected = dispatch_states.get(old)
        if expected is None or event.get("phase") != expected[1]:
            raise StateError(f"dispatch is invalid from {old}")
        current["state"] = {"implementation": "implementation_running", "review": "review_running", "correction": "correction_running", "rereview": "rereview_running"}[expected[1]]
        current["active"] = {"job_id": event.get("job_id"), "invocation_id": event.get("invocation_id"), "phase": expected[1]}
        current["invocation_count"] += 1
        if current["invocation_count"] > current["limits"]["max_invocations"]:
            raise StateError("invocation limit exceeded")
    elif event_type == "heartbeat" and old.endswith("_running"):
        current["last_heartbeat"] = event.get("timestamp")
    elif event_type == "worker_complete" and old.endswith("_running"):
        phase = current["active"]["phase"]
        current["state"] = {"implementation": "implementation_validating", "review": "review_validating", "correction": "correction_validating", "rereview": "rereview_validating"}[phase]
        current["active"] = None
        current["last_receipt"] = event.get("receipt_id")
        current["worker_outcome"] = event.get("outcome")
    elif event_type == "worker_failed" and old.endswith("_running"):
        current["failed_invocations"] += 1
        if current["failed_invocations"] > current["limits"]["max_failed_invocations"]:
            current["state"] = "escalated"; current["terminal_reason"] = "failed invocation limit exceeded"
        else:
            phase = current["active"]["phase"]
            current["state"] = {"implementation": "planned", "review": "review_dispatching", "correction": "correction_dispatching", "rereview": "rereview_dispatching"}[phase]
            current["active"] = None
    elif event_type == "receipt_validated":
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
        current["state"] = "milestone_complete"
    elif event_type == "advance_milestone" and old == "milestone_complete":
        current["milestone_index"] += 1
        current["milestone_id"] = str(event["milestone_id"])
        current["implementer"], current["reviewer"] = current["reviewer"], current["implementer"]
        current["round"] = 0; current["invocation_count"] = 0; current["failed_invocations"] = 0; current["correction_reviews"] = {}
        current["state"] = "planned"
    elif event_type in {"block", "escalate", "cancel"}:
        current["state"] = {"block": "blocked", "escalate": "escalated", "cancel": "cancelled"}[event_type]
        current["terminal_reason"] = event.get("reason", event_type)
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
