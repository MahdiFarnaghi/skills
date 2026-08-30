#!/usr/bin/env python3
"""Append-only Terra decision request/record protocol.

The protocol intentionally stores immutable evidence.  Supersession is a new
record pointing at an older request; no existing JSONL line is rewritten.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROLES = frozenset({"technical_authority", "orchestrator", "luna_worker", "claude_worker"})
PROFILE_KEYS = frozenset({"provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest"})
REQUEST_KEYS = frozenset({
    "schema_version", "record_type", "request_id", "idempotency_key", "task_id", "milestone_id",
    "milestone_index", "requested_by", "authority_role", "question", "reason", "binding",
    "status", "supersedes_request_id",
})
DECISION_KEYS = frozenset({
    "schema_version", "record_type", "decision_id", "idempotency_key", "request_id", "task_id",
    "milestone_id", "milestone_index", "decided_by", "authority_role", "authority_available",
    "decision", "rationale", "binding", "status",
})
UNAVAILABLE_KEYS = frozenset({
    "schema_version", "record_type", "availability_id", "idempotency_key", "request_id", "task_id",
    "milestone_id", "milestone_index", "reported_by", "authority_role", "reason", "binding", "status",
})
BINDING_KEYS = frozenset({"profiles", "worktree", "path_scope", "target_fingerprint", "policy_digest"})


class DecisionError(ValueError):
    """Stable protocol rejection."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _closed(value: Mapping[str, Any], required: frozenset[str], allowed: frozenset[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise DecisionError("E_DECISION_SCHEMA", f"{label} missing: {', '.join(missing)}")
    if unknown:
        raise DecisionError("E_DECISION_SCHEMA", f"{label} has unknown keys: {', '.join(unknown)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionError("E_DECISION_SCHEMA", f"{label} must be a non-empty string")
    return value


def _digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise DecisionError("E_DECISION_SCHEMA", f"{label} must be a lowercase SHA-256 digest")


def _profiles(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != ROLES:
        raise DecisionError("E_DECISION_BINDING", "profiles must contain exactly the four v2 roles")
    result = {}
    identity_fields = ("role_instance_id", "session_id", "host_id", "context_id")
    for role in sorted(ROLES):
        item = value[role]
        if not isinstance(item, dict) or set(item) != PROFILE_KEYS | frozenset({"role_instance_id", "session_id", "host_id", "context_id"}):
            raise DecisionError("E_DECISION_BINDING", f"profiles.{role} must contain identity fields and resolved profile fields")
        for field in identity_fields:
            _string(item[field], f"profiles.{role}.{field}")
        for field in PROFILE_KEYS:
            _string(item[field], f"profiles.{role}.{field}")
        _digest(item["profile_digest"], f"profiles.{role}.profile_digest")
        result[role] = item
    for field in identity_fields:
        values = [result[role][field] for role in ROLES]
        if len(set(values)) != len(values):
            raise DecisionError("E_DECISION_IDENTITY_REUSE", f"profiles.{field} must be distinct across all four roles")
    return result


def _binding(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DecisionError("E_DECISION_BINDING", "binding must be an object")
    _closed(value, BINDING_KEYS, BINDING_KEYS, "binding")
    _profiles(value["profiles"])
    worktree = _string(value["worktree"], "binding.worktree")
    if not Path(worktree).is_absolute():
        raise DecisionError("E_DECISION_BINDING", "binding.worktree must be absolute")
    scope = value["path_scope"]
    if not isinstance(scope, list) or not scope or len(set(scope)) != len(scope) or any(not isinstance(item, str) or not item for item in scope):
        raise DecisionError("E_DECISION_BINDING", "binding.path_scope must be a unique non-empty list")
    _digest(value["target_fingerprint"], "binding.target_fingerprint")
    _digest(value["policy_digest"], "binding.policy_digest")
    return value


def _common(record: Mapping[str, Any], required: frozenset[str], allowed: frozenset[str], label: str) -> None:
    _closed(record, required, allowed, label)
    if record["schema_version"] != 2:
        raise DecisionError("E_DECISION_SCHEMA", f"{label}.schema_version must be 2")
    _string(record["idempotency_key"], f"{label}.idempotency_key")
    _string(record["task_id"], f"{label}.task_id")
    _string(record["milestone_id"], f"{label}.milestone_id")
    if type(record["milestone_index"]) is not int or record["milestone_index"] < 1:
        raise DecisionError("E_DECISION_SCHEMA", f"{label}.milestone_index must be >= 1")
    _binding(record["binding"])
    if record["authority_role"] != "technical_authority":
        raise DecisionError("E_DECISION_AUTHORITY", "only technical_authority/Terra may decide")


def validate_decision_request(record: Any) -> Mapping[str, Any]:
    if not isinstance(record, dict):
        raise DecisionError("E_DECISION_SCHEMA", "request must be an object")
    _common(record, REQUEST_KEYS, REQUEST_KEYS, "decision request")
    _string(record["request_id"], "request_id")
    _string(record["requested_by"], "requested_by")
    _string(record["question"], "question")
    _string(record["reason"], "reason")
    if record["status"] != "pending":
        raise DecisionError("E_DECISION_STATE", "new decision requests must be pending")
    supersedes = record["supersedes_request_id"]
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes):
        raise DecisionError("E_DECISION_STATE", "supersedes_request_id must be null or a request id")
    return record


def validate_decision_record(record: Any) -> Mapping[str, Any]:
    if not isinstance(record, dict):
        raise DecisionError("E_DECISION_SCHEMA", "decision record must be an object")
    _common(record, DECISION_KEYS, DECISION_KEYS, "decision record")
    _string(record["decision_id"], "decision_id")
    _string(record["request_id"], "request_id")
    _string(record["decided_by"], "decided_by")
    if record["decided_by"] != "terra":
        raise DecisionError("E_DECISION_AUTHORITY", "decided_by must be terra")
    if record["authority_available"] is not True:
        raise DecisionError("E_DECISION_AUTHORITY_UNAVAILABLE", "unavailable Terra cannot emit a decision")
    if record["decision"] not in {"accept", "reject", "defer", "escalate"}:
        raise DecisionError("E_DECISION_VALUE", "decision must be accept, reject, defer, or escalate")
    _string(record["rationale"], "rationale")
    if record["status"] != "decided":
        raise DecisionError("E_DECISION_STATE", "decision record status must be decided")
    return record


def validate_unavailable_record(record: Any) -> Mapping[str, Any]:
    if not isinstance(record, dict):
        raise DecisionError("E_DECISION_SCHEMA", "unavailable record must be an object")
    _common(record, UNAVAILABLE_KEYS, UNAVAILABLE_KEYS, "authority unavailable record")
    _string(record["availability_id"], "availability_id")
    _string(record["request_id"], "request_id")
    _string(record["reported_by"], "reported_by")
    _string(record["reason"], "reason")
    if record["status"] != "authority_unavailable":
        raise DecisionError("E_DECISION_STATE", "unavailable record has an invalid status")
    return record


def validate_record(record: Any) -> Mapping[str, Any]:
    if not isinstance(record, dict):
        raise DecisionError("E_DECISION_SCHEMA", "record must be an object")
    kind = record.get("record_type")
    if kind == "request":
        return validate_decision_request(record)
    if kind == "decision":
        return validate_decision_record(record)
    if kind == "authority_unavailable":
        return validate_unavailable_record(record)
    raise DecisionError("E_DECISION_SCHEMA", "record_type must be request, decision, or authority_unavailable")


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def append_decision_records(path: str | Path, records: list[Mapping[str, Any]]) -> None:
    """Validate and append records atomically; retries are idempotent."""

    store = Path(path)
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing: list[Mapping[str, Any]] = []
        by_key: dict[str, str] = {}
        by_request: dict[str, Mapping[str, Any]] = {}
        decisions: set[str] = set()
        for line_number, line in enumerate(handle, 1):
            try:
                item = validate_record(json.loads(line))
            except (json.JSONDecodeError, DecisionError) as exc:
                raise DecisionError("E_DECISION_STORE", f"invalid JSONL line {line_number}") from exc
            canonical = _canonical(item)
            key = item["idempotency_key"]
            if key in by_key and by_key[key] != hashlib.sha256(canonical.encode()).hexdigest():
                raise DecisionError("E_DECISION_IDEMPOTENCY_CONFLICT", f"idempotency key {key!r} was reused with different content")
            by_key[key] = hashlib.sha256(canonical.encode()).hexdigest()
            existing.append(item)
            if item["record_type"] == "request":
                by_request[item["request_id"]] = item
            elif item["record_type"] == "decision":
                decisions.add(item["request_id"])
        pending = list(existing)
        for raw in records:
            item = validate_record(raw)
            canonical = _canonical(item)
            key = item["idempotency_key"]
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            if key in by_key:
                if by_key[key] != digest:
                    raise DecisionError("E_DECISION_IDEMPOTENCY_CONFLICT", f"idempotency key {key!r} already exists with different content")
                continue
            if item["record_type"] == "request":
                supersedes = item["supersedes_request_id"]
                if supersedes:
                    old = by_request.get(supersedes)
                    if old is None:
                        raise DecisionError("E_DECISION_SUPERSESSION", "superseded request does not exist")
                    if old["task_id"] != item["task_id"] or old["milestone_id"] != item["milestone_id"] or old["binding"] != item["binding"]:
                        raise DecisionError("E_DECISION_SUPERSESSION", "supersession must preserve task, milestone, and binding")
                    if supersedes in decisions:
                        raise DecisionError("E_DECISION_SUPERSESSION", "a decided request cannot be superseded")
                by_request[item["request_id"]] = item
            elif item["record_type"] == "decision":
                request = by_request.get(item["request_id"])
                if request is None:
                    raise DecisionError("E_DECISION_REQUEST", "decision must reference an existing request")
                if request.get("supersedes_request_id") == item["request_id"] or any(r.get("record_type") == "request" and r.get("supersedes_request_id") == item["request_id"] for r in pending):
                    raise DecisionError("E_DECISION_SUPERSEDED", "superseded request cannot receive a decision")
                if request["binding"] != item["binding"] or request["task_id"] != item["task_id"] or request["milestone_id"] != item["milestone_id"]:
                    raise DecisionError("E_DECISION_BINDING", "decision binding does not match its request")
                if item["request_id"] in decisions:
                    raise DecisionError("E_DECISION_STATE", "request already has a decision")
                decisions.add(item["request_id"])
            elif item["record_type"] == "authority_unavailable" and item["request_id"] not in by_request:
                raise DecisionError("E_DECISION_REQUEST", "unavailability record must reference an existing request")
            handle.write(canonical + "\n")
            by_key[key] = digest
            pending.append(item)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and append Terra decision JSONL records")
    parser.add_argument("--store", required=True)
    parser.add_argument("--record", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        records = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.record]
        append_decision_records(args.store, records)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DecisionError) as exc:
        print(str(exc))
        return 2
    print(json.dumps({"status": "valid", "records": len(records)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
