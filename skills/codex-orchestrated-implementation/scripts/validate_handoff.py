#!/usr/bin/env python3
"""Validate one frozen implementation/review handoff and its ledger entry."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from validate_config import ResolvedConfig, load_config

HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_REQUIRED = frozenset(
    {
        "schema_version", "task_id", "milestone_id", "milestone_index", "round",
        "invocation_id", "invocation_kind", "pair_member", "provider", "model", "role",
        "worktree", "baseline", "path_scope", "target_fingerprint_before",
        "target_fingerprint_after", "read_only", "policy_digest",
        "resolved_orchestrator_profile", "outcome",
    }
)
RECEIPT_ALLOWED = RECEIPT_REQUIRED | frozenset(
    {"files_changed", "files_inspected", "commands", "failures", "warnings", "limitations", "findings", "next_action"}
)
V2_RECEIPT_REQUIRED = RECEIPT_REQUIRED | frozenset({
    "full_worktree_fingerprint_before", "full_worktree_fingerprint_after",
    "role_instance_id", "session_id", "host_id", "context_id", "resolved_profile",
})
V2_RECEIPT_ALLOWED = V2_RECEIPT_REQUIRED | frozenset(
    {"files_changed", "files_inspected", "commands", "failures", "warnings", "limitations", "findings", "next_action", "capability_preflight", "checks_performed", "checks_supplied_by_orchestrator", "checks_unavailable"}
)
LEDGER_REQUIRED = frozenset(
    {
        "schema_version", "task_id", "milestone_id", "milestone_index", "round",
        "round_kind", "invocation_count", "max_invocations", "max_correction_rounds",
        "max_correction_reviews_per_defect", "worktree", "baseline", "path_scope",
        "implementation_target_fingerprint", "review_target_fingerprint_before",
        "review_target_fingerprint_after", "policy_digest", "resolved_orchestrator_profile",
        "implementer", "reviewer", "implementation_invocation_id", "review_invocation_id",
        "correction_review_counts", "status",
    }
)
LEDGER_ALLOWED = LEDGER_REQUIRED | frozenset({"finding_ids"})
V2_LEDGER_REQUIRED = LEDGER_REQUIRED | frozenset({"runtime_bindings"})
V2_LEDGER_ALLOWED = V2_LEDGER_REQUIRED | frozenset({"finding_ids"})
PROFILE_KEYS = frozenset(
    {"provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest"}
)
RUNTIME_ROLES = frozenset({"technical_authority", "orchestrator", "luna_worker", "claude_worker"})
RUNTIME_BINDING_KEYS = frozenset({"role", "role_instance_id", "session_id", "host_id", "context_id", "resolved_profile"})


class HandoffError(ValueError):
    """A stable, loud handoff validation error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must be a JSON object")
    return value


def _closed(value: Mapping[str, Any], required: frozenset[str], allowed: frozenset[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} missing required field(s): {', '.join(missing)}")
    if unknown:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} has unknown field(s): {', '.join(unknown)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must be an integer >= {minimum}")
    return value


def _digest(value: Any, label: str) -> str:
    digest = _string(value, label)
    if not HEX64.fullmatch(digest):
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must be a lowercase SHA-256 digest")
    return digest


def _baseline(value: Any, label: str) -> Mapping[str, Any]:
    baseline = _mapping(value, label)
    _closed(baseline, frozenset({"head_commit", "dirty_state_fingerprint"}), frozenset({"head_commit", "dirty_state_fingerprint"}), label)
    head = _string(baseline["head_commit"], f"{label}.head_commit")
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.head_commit must be a full Git object id")
    _digest(baseline["dirty_state_fingerprint"], f"{label}.dirty_state_fingerprint")
    return baseline


def _scope(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must be a non-empty list")
    if any(not isinstance(path, str) or not path for path in value) or len(set(value)) != len(value):
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label} must contain unique non-empty paths")
    return value


def _profile(value: Any, label: str) -> Mapping[str, Any]:
    profile = _mapping(value, label)
    _closed(profile, PROFILE_KEYS, PROFILE_KEYS, label)
    for key in PROFILE_KEYS - {"profile_digest"}:
        _string(profile[key], f"{label}.{key}")
    _digest(profile["profile_digest"], f"{label}.profile_digest")
    return profile


def _runtime_binding(value: Any, label: str) -> Mapping[str, Any]:
    binding = _mapping(value, label)
    _closed(binding, RUNTIME_BINDING_KEYS, RUNTIME_BINDING_KEYS, label)
    role = _string(binding["role"], f"{label}.role")
    if role not in RUNTIME_ROLES:
        raise HandoffError("E_RUNTIME_ROLE", f"{label}.role is not a v2 control-plane or pair role")
    for key in ("role_instance_id", "session_id", "host_id", "context_id"):
        _string(binding[key], f"{label}.{key}")
    _profile(binding["resolved_profile"], f"{label}.resolved_profile")
    return binding


def _runtime_bindings(value: Any, label: str = "runtime_bindings") -> Mapping[str, Mapping[str, Any]]:
    bindings = _mapping(value, label)
    if set(bindings) != RUNTIME_ROLES:
        raise HandoffError("E_RUNTIME_BINDING", f"{label} must bind exactly {', '.join(sorted(RUNTIME_ROLES))}")
    result: dict[str, Mapping[str, Any]] = {}
    for role in sorted(RUNTIME_ROLES):
        binding = _runtime_binding(bindings[role], f"{label}.{role}")
        _same(binding["role"], role, f"{label}.{role}.role")
        result[role] = binding
    for key in ("role_instance_id", "session_id", "host_id", "context_id"):
        values = [result[role][key] for role in RUNTIME_ROLES]
        if len(set(values)) != len(values):
            raise HandoffError("E_RUNTIME_IDENTITY_REUSE", f"runtime {key} values must be distinct across all four roles")
    return result


def _receipt(value: Any, label: str) -> Mapping[str, Any]:
    receipt = _mapping(value, label)
    version = receipt.get("schema_version")
    if version == 2:
        _closed(receipt, V2_RECEIPT_REQUIRED, V2_RECEIPT_ALLOWED, label)
    else:
        _closed(receipt, RECEIPT_REQUIRED, RECEIPT_ALLOWED, label)
    if version not in {1, 2}:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.schema_version must be 1 or 2")
    for key in ("task_id", "milestone_id", "invocation_id", "pair_member", "provider", "model", "role", "outcome"):
        _string(receipt[key], f"{label}.{key}")
    _integer(receipt["milestone_index"], f"{label}.milestone_index", 1)
    _integer(receipt["round"], f"{label}.round", 0)
    if receipt["invocation_kind"] not in {"implementation", "review"}:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.invocation_kind must be implementation or review")
    if receipt["role"] not in {"implementer", "reviewer"}:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.role must be implementer or reviewer")
    if type(receipt["read_only"]) is not bool:
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.read_only must be boolean")
    worktree = _string(receipt["worktree"], f"{label}.worktree")
    if not Path(worktree).is_absolute():
        raise HandoffError("E_HANDOFF_SCHEMA", f"{label}.worktree must be absolute")
    _baseline(receipt["baseline"], f"{label}.baseline")
    _scope(receipt["path_scope"], f"{label}.path_scope")
    _digest(receipt["target_fingerprint_before"], f"{label}.target_fingerprint_before")
    _digest(receipt["target_fingerprint_after"], f"{label}.target_fingerprint_after")
    _digest(receipt["policy_digest"], f"{label}.policy_digest")
    _profile(receipt["resolved_orchestrator_profile"], f"{label}.resolved_orchestrator_profile")
    if version == 2:
        for key in ("role_instance_id", "session_id", "host_id", "context_id"):
            _string(receipt[key], f"{label}.{key}")
        _profile(receipt["resolved_profile"], f"{label}.resolved_profile")
        _digest(receipt["full_worktree_fingerprint_before"], f"{label}.full_worktree_fingerprint_before")
        _digest(receipt["full_worktree_fingerprint_after"], f"{label}.full_worktree_fingerprint_after")
        if "capability_preflight" in receipt:
            capabilities = _mapping(receipt["capability_preflight"], f"{label}.capability_preflight")
            expected = {"exact_snapshot_access", "repository_inspection", "shell_execution", "test_execution", "crg_checks"}
            if set(capabilities) != expected or any(type(value) is not bool for value in capabilities.values()):
                raise HandoffError("E_REVIEW_CAPABILITY", f"{label}.capability_preflight must contain five boolean capabilities")
        for key in ("checks_performed", "checks_supplied_by_orchestrator", "checks_unavailable"):
            if key in receipt and (not isinstance(receipt[key], list) or any(not isinstance(item, str) for item in receipt[key])):
                raise HandoffError("E_REVIEW_EVIDENCE", f"{label}.{key} must be a list of strings")
    return receipt


def _ledger(value: Any) -> Mapping[str, Any]:
    ledger = _mapping(value, "ledger")
    version = ledger.get("schema_version")
    if version == 2:
        _closed(ledger, V2_LEDGER_REQUIRED, V2_LEDGER_ALLOWED, "ledger")
    else:
        _closed(ledger, LEDGER_REQUIRED, LEDGER_ALLOWED, "ledger")
    if version not in {1, 2}:
        raise HandoffError("E_HANDOFF_SCHEMA", "ledger.schema_version must be 1 or 2")
    for key in ("task_id", "milestone_id", "implementer", "reviewer", "implementation_invocation_id", "review_invocation_id", "status"):
        _string(ledger[key], f"ledger.{key}")
    _integer(ledger["milestone_index"], "ledger.milestone_index", 1)
    _integer(ledger["round"], "ledger.round", 0)
    if ledger["round_kind"] not in {"initial", "correction"}:
        raise HandoffError("E_HANDOFF_SCHEMA", "ledger.round_kind must be initial or correction")
    _integer(ledger["invocation_count"], "ledger.invocation_count", 0)
    if ledger["max_invocations"] != 48 or ledger["max_correction_rounds"] != 20 or ledger["max_correction_reviews_per_defect"] != 3:
        raise HandoffError("E_HANDOFF_LIMIT", "ledger must retain limits 48 invocations, 20 correction rounds, and 3 reviews per defect")
    worktree = _string(ledger["worktree"], "ledger.worktree")
    if not Path(worktree).is_absolute():
        raise HandoffError("E_HANDOFF_SCHEMA", "ledger.worktree must be absolute")
    _baseline(ledger["baseline"], "ledger.baseline")
    _scope(ledger["path_scope"], "ledger.path_scope")
    for key in ("implementation_target_fingerprint", "review_target_fingerprint_before", "review_target_fingerprint_after", "policy_digest"):
        _digest(ledger[key], f"ledger.{key}")
    _profile(ledger["resolved_orchestrator_profile"], "ledger.resolved_orchestrator_profile")
    counts = _mapping(ledger["correction_review_counts"], "ledger.correction_review_counts")
    for finding_id, count in counts.items():
        _string(finding_id, "ledger.correction_review_counts key")
        if type(count) is not int or count < 0 or count > 3:
            raise HandoffError("E_HANDOFF_LIMIT", "no defect may receive more than three correction reviews")
    if version == 2:
        _runtime_bindings(ledger["runtime_bindings"])
    return ledger


def _same(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise HandoffError("E_HANDOFF_MISMATCH", f"{label} does not match the frozen handoff")


def _git_head(worktree: str) -> str:
    try:
        inside = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--is-inside-work-tree"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HandoffError("E_GIT_REQUIRED", f"{worktree} is not an accessible Git worktree") from exc
    if inside != "true" or not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise HandoffError("E_GIT_REQUIRED", f"{worktree} is not an accessible Git worktree")
    return head


def _git_worktree_root(worktree: str) -> Path:
    """Return the exact Git worktree root or fail loudly."""

    try:
        root = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HandoffError("E_GIT_REQUIRED", f"{worktree} is not an accessible Git worktree") from exc
    supplied = Path(worktree).resolve()
    resolved_root = Path(root).resolve()
    if supplied != resolved_root:
        raise HandoffError("E_GIT_REQUIRED", f"{worktree} must name the Git worktree root")
    return resolved_root


def _normalized_scope(worktree: str, scope: list[str]) -> list[str]:
    root = _git_worktree_root(worktree)
    normalized: list[str] = []
    for raw in scope:
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HandoffError("E_HANDOFF_SCOPE", f"scope path {raw!r} must be relative and stay within the worktree")
        path = candidate.as_posix()
        if path in {"", "."}:
            path = "."
        absolute = (root / path).absolute()
        if os.path.commonpath((str(root), str(absolute))) != str(root):
            raise HandoffError("E_HANDOFF_SCOPE", f"scope path {raw!r} escapes the worktree")
        normalized.append(path)
    return sorted(normalized)


def _frame(hasher: hashlib._Hash, tag: str, payload: bytes) -> None:
    hasher.update(tag.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(len(payload)).encode("ascii"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")


def _snapshot_path(hasher: hashlib._Hash, path: Path, display: str) -> None:
    """Hash a worktree path without following symlinks or using timestamps."""

    try:
        stat = path.lstat()
    except FileNotFoundError:
        _frame(hasher, "missing", display.encode())
        return
    mode = oct(stat.st_mode & 0o7777).encode()
    if path.is_symlink():
        _frame(hasher, "symlink", display.encode() + b"\0" + mode + b"\0" + os.readlink(path).encode())
        return
    if path.is_file():
        _frame(hasher, "file", display.encode() + b"\0" + mode)
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                _frame(hasher, "content", chunk)
        return
    if path.is_dir():
        _frame(hasher, "directory", display.encode() + b"\0" + mode)
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            # Git metadata is not working-tree content even for a '.' scope.
            if child.name == ".git":
                continue
            child_display = child.name if display == "." else f"{display}/{child.name}"
            _snapshot_path(hasher, child, child_display)
        return
    raise HandoffError("E_HANDOFF_SCOPE", f"unsupported filesystem object in scope: {display}")


def git_worktree_fingerprint(worktree: str, scope: list[str]) -> str:
    """Return a deterministic SHA-256 for Git state and bytes in ``scope``.

    The digest binds the declared scope, index entries, porcelain status (which
    records staged/unstaged/deleted/untracked state), and a byte-level current
    worktree manifest. It intentionally excludes paths outside ``scope``.
    """

    root = _git_worktree_root(worktree)
    normalized = _normalized_scope(worktree, scope)
    hasher = hashlib.sha256()
    _frame(hasher, "format", b"codex-orchestration-git-scope-v1")
    for path in normalized:
        _frame(hasher, "scope", path.encode())
    try:
        index = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-s", "-z", "--", *normalized],
            check=True, capture_output=True,
        ).stdout
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *normalized],
            check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HandoffError("E_GIT_REQUIRED", f"cannot read Git state for {root}") from exc
    _frame(hasher, "index", index)
    _frame(hasher, "status", status)
    for path in normalized:
        _snapshot_path(hasher, root / path, path)
    return hasher.hexdigest()


def validate_handoff(config: ResolvedConfig, implementation_value: Any, review_value: Any, ledger_value: Any) -> None:
    """Validate one complete, immutable handoff against the resolved policy."""

    implementation = _receipt(implementation_value, "implementation receipt")
    review = _receipt(review_value, "review receipt")
    ledger = _ledger(ledger_value)
    if (config.policy.get("version") == 2) != (ledger["schema_version"] == 2):
        raise HandoffError("E_HANDOFF_SCHEMA", "v2 policy requires v2 receipts and ledger; v1 is not silently reinterpreted")
    members = {member["name"]: member for member in config.policy["pair"]}
    names = list(members)
    first = config.policy["workflow"]["first_implementer"]
    other = next(name for name in names if name != first)
    milestone_index = ledger["milestone_index"]
    expected_implementer = first if milestone_index % 2 else other
    expected_reviewer = other if expected_implementer == first else first

    for receipt, label in ((implementation, "implementation receipt"), (review, "review receipt")):
        _same(receipt["policy_digest"], config.policy_digest, f"{label}.policy_digest")
        _same(receipt["resolved_orchestrator_profile"], ledger["resolved_orchestrator_profile"], f"{label}.resolved_orchestrator_profile")
        for key in ("task_id", "milestone_id", "milestone_index", "round", "worktree", "baseline", "path_scope"):
            _same(receipt[key], ledger[key], f"{label}.{key}")

    if ledger["schema_version"] == 2:
        bindings = _runtime_bindings(ledger["runtime_bindings"])
        configured = {member["name"]: member for member in config.policy["pair"]}
        control_plane = {
            "technical_authority": config.policy["technical_authority"],
            "orchestrator": config.policy["orchestrator"],
            **{name: configured[name] for name in ("luna_worker", "claude_worker")},
        }
        for role, binding in bindings.items():
            profile = binding["resolved_profile"]
            requested = control_plane[role]
            profile_keys = ("provider", "model", "reasoning_effort") if "reasoning_effort" in requested else ("provider", "model")
            for key in profile_keys:
                _same(profile[key], requested[key], f"runtime_bindings.{role}.resolved_profile.{key}")
        for receipt, label in ((implementation, "implementation receipt"), (review, "review receipt")):
            role = receipt["pair_member"]
            binding = bindings[role]
            for key in ("role_instance_id", "session_id", "host_id", "context_id", "resolved_profile"):
                _same(receipt[key], binding[key], f"{label}.{key} versus runtime_bindings.{role}.{key}")
        if ledger["resolved_orchestrator_profile"] != bindings["orchestrator"]["resolved_profile"]:
            raise HandoffError("E_RUNTIME_BINDING", "resolved_orchestrator_profile must be Luna's resolved orchestrator profile")

    _same(ledger["policy_digest"], config.policy_digest, "ledger.policy_digest")
    _same(implementation["invocation_kind"], "implementation", "implementation receipt.invocation_kind")
    _same(implementation["role"], "implementer", "implementation receipt.role")
    _same(implementation["read_only"], False, "implementation receipt.read_only")
    _same(review["invocation_kind"], "review", "review receipt.invocation_kind")
    _same(review["role"], "reviewer", "review receipt.role")
    _same(review["read_only"], True, "review receipt.read_only")
    if implementation["outcome"] != "success":
        raise HandoffError("E_HANDOFF_OUTCOME", "a completed implementation handoff requires outcome 'success'")
    expected_status = {"approve": "reviewed", "needs_correction": "needs_correction"}
    if review["outcome"] not in expected_status:
        raise HandoffError("E_HANDOFF_OUTCOME", "a completed review requires outcome 'approve' or 'needs_correction'")
    _same(ledger["status"], expected_status[review["outcome"]], "ledger.status for review outcome")
    _same(implementation["pair_member"], expected_implementer, "implementation receipt.pair_member")
    _same(review["pair_member"], expected_reviewer, "review receipt.pair_member")
    _same(ledger["implementer"], expected_implementer, "ledger.implementer")
    _same(ledger["reviewer"], expected_reviewer, "ledger.reviewer")
    _same(ledger["implementation_invocation_id"], implementation["invocation_id"], "ledger.implementation_invocation_id")
    _same(ledger["review_invocation_id"], review["invocation_id"], "ledger.review_invocation_id")
    for receipt, label in ((implementation, "implementation receipt"), (review, "review receipt")):
        member = members[receipt["pair_member"]]
        _same(receipt["provider"], member["provider"], f"{label}.provider")
        _same(receipt["model"], member["model"], f"{label}.model")

    _same(review["target_fingerprint_before"], implementation["target_fingerprint_after"], "review fingerprint before")
    _same(review["target_fingerprint_after"], review["target_fingerprint_before"], "review fingerprint after")
    _same(ledger["implementation_target_fingerprint"], implementation["target_fingerprint_after"], "ledger implementation fingerprint")
    _same(ledger["review_target_fingerprint_before"], review["target_fingerprint_before"], "ledger review fingerprint before")
    _same(ledger["review_target_fingerprint_after"], review["target_fingerprint_after"], "ledger review fingerprint after")
    if (ledger["round"] == 0) != (ledger["round_kind"] == "initial"):
        raise HandoffError("E_HANDOFF_LIMIT", "round 0 must be initial; later rounds must be correction")
    if ledger["round"] > 20 or ledger["invocation_count"] > 48:
        raise HandoffError("E_HANDOFF_LIMIT", "milestone correction-round or invocation limit exceeded")
    if ledger["invocation_count"] < (ledger["round"] + 1) * 2:
        raise HandoffError("E_HANDOFF_LIMIT", "ledger invocation_count omits a completed pair round")
    _same(_git_head(ledger["worktree"]), ledger["baseline"]["head_commit"], "Git HEAD")
    current_fingerprint = git_worktree_fingerprint(ledger["worktree"], ledger["path_scope"])
    _same(current_fingerprint, implementation["target_fingerprint_after"], "current worktree fingerprint versus implementation handoff")
    _same(current_fingerprint, review["target_fingerprint_before"], "current worktree fingerprint versus review before")
    _same(current_fingerprint, review["target_fingerprint_after"], "current worktree fingerprint versus review after")
    if ledger["schema_version"] == 2:
        full_fingerprint = git_worktree_fingerprint(ledger["worktree"], ["."])
        _same(review["full_worktree_fingerprint_before"], implementation["full_worktree_fingerprint_after"], "review full-worktree fingerprint before")
        _same(review["full_worktree_fingerprint_after"], review["full_worktree_fingerprint_before"], "review full-worktree fingerprint after")
        _same(full_fingerprint, review["full_worktree_fingerprint_before"], "current full-worktree fingerprint versus review before")
        _same(full_fingerprint, review["full_worktree_fingerprint_after"], "current full-worktree fingerprint versus review after")


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def append_receipts_idempotently(path: str | Path, receipts: list[Mapping[str, Any]]) -> None:
    """Append only unseen receipts; identical invocation re-entry is a no-op."""

    store = Path(path)
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        known: dict[str, str] = {}
        for number, line in enumerate(handle, 1):
            try:
                existing = _mapping(json.loads(line), f"receipt store line {number}")
                invocation_id = _string(existing.get("invocation_id"), f"receipt store line {number}.invocation_id")
            except (json.JSONDecodeError, HandoffError) as exc:
                raise HandoffError("E_RECEIPT_STORE", f"invalid receipt store line {number}") from exc
            digest = hashlib.sha256(_canonical(existing).encode()).hexdigest()
            if invocation_id in known and known[invocation_id] != digest:
                raise HandoffError("E_RECEIPT_STORE", f"receipt store reuses {invocation_id!r} with different content")
            known[invocation_id] = digest
        for receipt in receipts:
            invocation_id = _string(receipt["invocation_id"], "receipt.invocation_id")
            canonical = _canonical(receipt)
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            if invocation_id in known:
                if known[invocation_id] != digest:
                    raise HandoffError("E_RECEIPT_STORE", f"invocation {invocation_id!r} already exists with different content")
                continue
            handle.write(canonical + "\n")
            known[invocation_id] = digest
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_ledger_idempotently(path: str | Path, ledger: Mapping[str, Any]) -> None:
    """Append one immutable ledger entry; identical re-entry is a no-op."""

    store = Path(path)
    store.parent.mkdir(parents=True, exist_ok=True)
    identity = ":".join((_string(ledger["task_id"], "ledger.task_id"), _string(ledger["milestone_id"], "ledger.milestone_id"), str(_integer(ledger["round"], "ledger.round"))))
    canonical = _canonical(ledger)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with store.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        known: dict[str, str] = {}
        for number, line in enumerate(handle, 1):
            try:
                existing = _mapping(json.loads(line), f"ledger store line {number}")
                existing_identity = ":".join((_string(existing.get("task_id"), "ledger store task_id"), _string(existing.get("milestone_id"), "ledger store milestone_id"), str(_integer(existing.get("round"), "ledger store round"))))
            except (json.JSONDecodeError, HandoffError) as exc:
                raise HandoffError("E_LEDGER_STORE", f"invalid ledger store line {number}") from exc
            existing_digest = hashlib.sha256(_canonical(existing).encode()).hexdigest()
            if existing_identity in known and known[existing_identity] != existing_digest:
                raise HandoffError("E_LEDGER_STORE", f"ledger store reuses {existing_identity!r} with different content")
            known[existing_identity] = existing_digest
        if identity in known:
            if known[identity] != digest:
                raise HandoffError("E_LEDGER_STORE", f"ledger entry {identity!r} already exists with different content")
        else:
            handle.write(canonical + "\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_json(path: str, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("E_HANDOFF_READ", f"cannot load {label} JSON from {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one immutable implementation/review handoff")
    parser.add_argument("--config")
    parser.add_argument("--implementation-receipt")
    parser.add_argument("--review-receipt")
    parser.add_argument("--ledger")
    parser.add_argument("--receipt-store", help="append-only JSONL receipt store")
    parser.add_argument("--append-receipts", action="store_true")
    parser.add_argument("--ledger-store", help="append-only JSONL ledger store")
    parser.add_argument("--append-ledger", action="store_true")
    parser.add_argument("--fingerprint-worktree", help="print a scoped Git worktree fingerprint")
    parser.add_argument("--scope-path", action="append", dest="scope_paths", help="relative scope path; repeat for each path")
    args = parser.parse_args(argv)
    if args.fingerprint_worktree:
        if not args.scope_paths:
            parser.error("--fingerprint-worktree requires at least one --scope-path")
        try:
            print(json.dumps({"worktree": str(Path(args.fingerprint_worktree).resolve()), "path_scope": sorted(args.scope_paths), "fingerprint": git_worktree_fingerprint(args.fingerprint_worktree, args.scope_paths)}, sort_keys=True))
        except HandoffError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if not all((args.config, args.implementation_receipt, args.review_receipt, args.ledger)):
        parser.error("--config, --implementation-receipt, --review-receipt, and --ledger are required unless fingerprinting")
    if args.append_receipts and not args.receipt_store:
        parser.error("--append-receipts requires --receipt-store")
    if args.append_ledger and not args.ledger_store:
        parser.error("--append-ledger requires --ledger-store")
    try:
        config = load_config(args.config)
        implementation = _load_json(args.implementation_receipt, "implementation receipt")
        review = _load_json(args.review_receipt, "review receipt")
        ledger = _load_json(args.ledger, "ledger")
        validate_handoff(config, implementation, review, ledger)
        if args.append_receipts:
            append_receipts_idempotently(args.receipt_store, [implementation, review])
        if args.append_ledger:
            append_ledger_idempotently(args.ledger_store, ledger)
    except (HandoffError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid", "milestone_id": ledger["milestone_id"], "round": ledger["round"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
