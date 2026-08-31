from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_validate_config import VALID  # noqa: E402
from validate_config import load_config  # noqa: E402
from validate_handoff import (  # noqa: E402
    HandoffError,
    append_ledger_idempotently,
    append_receipts_idempotently,
    git_worktree_fingerprint,
    main,
    validate_handoff,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def records(tmp_path: Path, *, milestone_index: int = 1, scope: list[str] | None = None) -> tuple[object, dict, dict, dict]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "initial")
    head = git(repo, "rev-parse", "HEAD")
    scope = scope or ["app.py"]
    fingerprint = git_worktree_fingerprint(str(repo), scope)
    config_path = tmp_path / ".codex-orchestration.toml"
    config_path.write_text(VALID, encoding="utf-8")
    config = load_config(config_path)
    profile = {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "host_identity": "host:test",
        "resolution_source": "host_metadata",
        "profile_digest": "c" * 64,
    }
    baseline = {"head_commit": head, "dirty_state_fingerprint": fingerprint}
    implementer, reviewer = ("worker_a", "worker_b") if milestone_index % 2 else ("worker_b", "worker_a")
    members = {member["name"]: member for member in config.policy["pair"]}
    common = {
        "schema_version": 1,
        "task_id": "task-1",
        "milestone_id": "m-1",
        "milestone_index": milestone_index,
        "round": 0,
        "worktree": str(repo),
        "baseline": baseline,
        "path_scope": scope,
        "policy_digest": config.policy_digest,
        "resolved_orchestrator_profile": profile,
    }
    implementation = {
        **common,
        "invocation_id": "impl-1",
        "invocation_kind": "implementation",
        "pair_member": implementer,
        "provider": members[implementer]["provider"],
        "model": members[implementer]["model"],
        "role": "implementer",
        "target_fingerprint_before": fingerprint,
        "target_fingerprint_after": fingerprint,
        "read_only": False,
        "outcome": "success",
    }
    review = {
        **common,
        "invocation_id": "review-1",
        "invocation_kind": "review",
        "pair_member": reviewer,
        "provider": members[reviewer]["provider"],
        "model": members[reviewer]["model"],
        "role": "reviewer",
        "target_fingerprint_before": fingerprint,
        "target_fingerprint_after": fingerprint,
        "read_only": True,
        "outcome": "approve",
    }
    ledger = {
        **common,
        "round_kind": "initial",
        "invocation_count": 2,
        "max_invocations": 48,
        "max_correction_rounds": 20,
        "max_correction_reviews_per_defect": 3,
        "implementation_target_fingerprint": fingerprint,
        "review_target_fingerprint_before": fingerprint,
        "review_target_fingerprint_after": fingerprint,
        "implementer": implementer,
        "reviewer": reviewer,
        "implementation_invocation_id": "impl-1",
        "review_invocation_id": "review-1",
        "correction_review_counts": {},
        "status": "reviewed",
    }
    return config, implementation, review, ledger


def assert_error(config: object, implementation: dict, review: dict, ledger: dict, code: str) -> None:
    with pytest.raises(HandoffError) as caught:
        validate_handoff(config, implementation, review, ledger)  # type: ignore[arg-type]
    assert caught.value.code == code


def test_accepts_frozen_handoff_and_alternating_pair(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    validate_handoff(config, implementation, review, ledger)
    config, implementation, review, ledger = records(tmp_path / "even", milestone_index=2)
    validate_handoff(config, implementation, review, ledger)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda implementation, review, ledger: review.update(read_only=False),
        lambda implementation, review, ledger: implementation.update(role="reviewer"),
        lambda implementation, review, ledger: review.update(pair_member="worker_a"),
    ],
)
def test_rejects_invalid_role_or_read_only_rules(tmp_path: Path, mutate: object) -> None:
    config, implementation, review, ledger = records(tmp_path)
    mutate(implementation, review, ledger)  # type: ignore[operator]
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")


def test_rejects_non_frozen_scope_or_review_fingerprint(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    review["path_scope"] = ["other.py"]
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")
    config, implementation, review, ledger = records(tmp_path / "second")
    review["target_fingerprint_after"] = "e" * 64
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")


def test_rejects_scoped_and_untracked_changes_after_receipt_creation(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    (Path(ledger["worktree"]) / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")
    config, implementation, review, ledger = records(tmp_path / "untracked", scope=["app.py", "later.txt"])
    (Path(ledger["worktree"]) / "later.txt").write_text("untracked\n", encoding="utf-8")
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")


def test_ignores_changes_outside_declared_scope(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    (Path(ledger["worktree"]) / "outside.txt").write_text("outside scope\n", encoding="utf-8")
    validate_handoff(config, implementation, review, ledger)


def test_fingerprint_covers_staged_and_deleted_scoped_content(tmp_path: Path) -> None:
    _, _, _, ledger = records(tmp_path)
    repo = Path(ledger["worktree"])
    initial = git_worktree_fingerprint(str(repo), ["app.py"])
    (repo / "app.py").write_text("print('staged')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    staged = git_worktree_fingerprint(str(repo), ["app.py"])
    assert staged != initial
    git(repo, "rm", "-f", "app.py")
    deleted = git_worktree_fingerprint(str(repo), ["app.py"])
    assert deleted != staged


def test_rejects_policy_profile_and_limit_drift(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    ledger["policy_digest"] = "f" * 64
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")
    config, implementation, review, ledger = records(tmp_path / "second")
    ledger["resolved_orchestrator_profile"] = {**ledger["resolved_orchestrator_profile"], "model": "gpt-5.6-sol"}
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")
    config, implementation, review, ledger = records(tmp_path / "third")
    ledger["invocation_count"] = 9
    assert_error(config, implementation, review, ledger, "E_HANDOFF_LIMIT")


@pytest.mark.parametrize(
    "outcome",
    ["failure", "transport_failure", "escalate"],
)
def test_rejects_non_successful_implementation_handoff(tmp_path: Path, outcome: str) -> None:
    config, implementation, review, ledger = records(tmp_path)
    implementation["outcome"] = outcome
    assert_error(config, implementation, review, ledger, "E_HANDOFF_OUTCOME")


def test_requires_review_outcome_and_matching_ledger_status(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    review["outcome"] = "failure"
    assert_error(config, implementation, review, ledger, "E_HANDOFF_OUTCOME")
    config, implementation, review, ledger = records(tmp_path / "correction")
    review["outcome"] = "needs_correction"
    ledger["status"] = "needs_correction"
    validate_handoff(config, implementation, review, ledger)
    ledger["status"] = "reviewed"
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")


def test_rejects_non_git_or_changed_baseline(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    ledger["baseline"] = {**ledger["baseline"], "head_commit": "f" * 40}
    implementation["baseline"] = ledger["baseline"]
    review["baseline"] = ledger["baseline"]
    assert_error(config, implementation, review, ledger, "E_HANDOFF_MISMATCH")


def test_append_only_store_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    config, implementation, review, ledger = records(tmp_path)
    validate_handoff(config, implementation, review, ledger)
    store = tmp_path / "receipts.jsonl"
    append_receipts_idempotently(store, [implementation, review])
    first = store.read_text(encoding="utf-8")
    append_receipts_idempotently(store, [implementation, review])
    assert store.read_text(encoding="utf-8") == first
    conflicting = copy.deepcopy(implementation)
    conflicting["outcome"] = "failure"
    with pytest.raises(HandoffError) as caught:
        append_receipts_idempotently(store, [conflicting])
    assert caught.value.code == "E_RECEIPT_STORE"


def test_append_only_ledger_store_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    _, _, _, ledger = records(tmp_path)
    store = tmp_path / "ledger.jsonl"
    append_ledger_idempotently(store, ledger)
    first = store.read_text(encoding="utf-8")
    append_ledger_idempotently(store, ledger)
    assert store.read_text(encoding="utf-8") == first
    conflicting = copy.deepcopy(ledger)
    conflicting["status"] = "accepted"
    with pytest.raises(HandoffError) as caught:
        append_ledger_idempotently(store, conflicting)
    assert caught.value.code == "E_LEDGER_STORE"


def test_cli_validates_and_appends_receipts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config, implementation, review, ledger = records(tmp_path)
    config_path = Path(config.path)  # type: ignore[attr-defined]
    implementation_path = tmp_path / "implementation.json"
    review_path = tmp_path / "review.json"
    ledger_path = tmp_path / "ledger.json"
    store = tmp_path / "receipts.jsonl"
    ledger_store = tmp_path / "ledger.jsonl"
    for path, value in ((implementation_path, implementation), (review_path, review), (ledger_path, ledger)):
        path.write_text(json.dumps(value), encoding="utf-8")
    args = [
        "--config", str(config_path),
        "--implementation-receipt", str(implementation_path),
        "--review-receipt", str(review_path),
        "--ledger", str(ledger_path),
        "--receipt-store", str(store),
        "--append-receipts",
        "--ledger-store", str(ledger_store),
        "--append-ledger",
    ]
    assert main(args) == 0
    assert '"status": "valid"' in capsys.readouterr().out
    assert len(store.read_text(encoding="utf-8").splitlines()) == 2
    assert len(ledger_store.read_text(encoding="utf-8").splitlines()) == 1
    assert main(args) == 0
    assert len(store.read_text(encoding="utf-8").splitlines()) == 2


def test_fingerprint_cli_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, _, _, ledger = records(tmp_path)
    assert main(["--fingerprint-worktree", ledger["worktree"], "--scope-path", "app.py"]) == 0
    output = capsys.readouterr().out
    assert '"fingerprint"' in output
    assert '"path_scope": ["app.py"]' in output
