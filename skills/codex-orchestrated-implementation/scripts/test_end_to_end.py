from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_VALIDATOR = SKILL_ROOT / "scripts" / "validate_config.py"
HANDOFF_VALIDATOR = SKILL_ROOT / "scripts" / "validate_handoff.py"


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def fingerprint(repo: Path, *scope: str) -> str:
    result = run(
        str(HANDOFF_VALIDATOR),
        "--fingerprint-worktree",
        str(repo),
        *[item for path in scope for item in ("--scope-path", path)],
    )
    return json.loads(result.stdout)["fingerprint"]


def test_cli_end_to_end_reciprocal_handoff_and_tamper_detection(tmp_path: Path) -> None:
    """Exercise the installed command-line contracts against a real Git worktree."""

    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "E2E Test")
    (repo / "app.py").write_text("print('baseline')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "baseline")
    head = git(repo, "rev-parse", "HEAD")

    config = repo / ".codex-orchestration.toml"
    config.write_text(
        """version = 1

[orchestrator]
provider = "codex"
model = "gpt-5.6-terra"
reasoning_effort = "high"

[[pair]]
name = "claude"
provider = "claude_code"
model = "opus"
enabled = true

[[pair]]
name = "luna"
provider = "codex"
model = "gpt-5.6-luna"
enabled = true

[workflow]
profile = "standard"
pairing_mode = "alternating"
first_implementer = "claude"
""",
        encoding="utf-8",
    )
    resolved_policy = json.loads(run(str(CONFIG_VALIDATOR), "--config", str(config)).stdout)

    baseline_fingerprint = fingerprint(repo, "app.py")
    (repo / "app.py").write_text("print('implemented')\n", encoding="utf-8")
    handoff_fingerprint = fingerprint(repo, "app.py")
    assert handoff_fingerprint != baseline_fingerprint

    profile = {
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "host_identity": "e2e-host",
        "resolution_source": "test",
        "profile_digest": "a" * 64,
    }
    common = {
        "schema_version": 1,
        "task_id": "e2e-task",
        "milestone_id": "m1",
        "milestone_index": 1,
        "round": 0,
        "worktree": str(repo),
        "baseline": {
            "head_commit": head,
            "dirty_state_fingerprint": baseline_fingerprint,
        },
        "path_scope": ["app.py"],
        "policy_digest": resolved_policy["policy_digest"],
        "resolved_orchestrator_profile": profile,
    }
    implementation = {
        **common,
        "invocation_id": "impl-1",
        "invocation_kind": "implementation",
        "pair_member": "claude",
        "provider": "claude_code",
        "model": "opus",
        "role": "implementer",
        "target_fingerprint_before": baseline_fingerprint,
        "target_fingerprint_after": handoff_fingerprint,
        "read_only": False,
        "outcome": "success",
    }
    review = {
        **common,
        "invocation_id": "review-1",
        "invocation_kind": "review",
        "pair_member": "luna",
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "role": "reviewer",
        "target_fingerprint_before": handoff_fingerprint,
        "target_fingerprint_after": handoff_fingerprint,
        "read_only": True,
        "outcome": "approve",
    }
    ledger = {
        **common,
        "round_kind": "initial",
        "invocation_count": 2,
        "max_invocations": 8,
        "max_correction_rounds": 20,
        "max_correction_reviews_per_defect": 3,
        "implementation_target_fingerprint": handoff_fingerprint,
        "review_target_fingerprint_before": handoff_fingerprint,
        "review_target_fingerprint_after": handoff_fingerprint,
        "implementer": "claude",
        "reviewer": "luna",
        "implementation_invocation_id": "impl-1",
        "review_invocation_id": "review-1",
        "correction_review_counts": {},
        "status": "reviewed",
    }
    implementation_path = tmp_path / "implementation.json"
    review_path = tmp_path / "review.json"
    ledger_path = tmp_path / "ledger.json"
    for path, record in ((implementation_path, implementation), (review_path, review), (ledger_path, ledger)):
        path.write_text(json.dumps(record), encoding="utf-8")
    receipt_store = tmp_path / "receipts.jsonl"
    ledger_store = tmp_path / "ledger.jsonl"
    command = (
        str(HANDOFF_VALIDATOR),
        "--config", str(config),
        "--implementation-receipt", str(implementation_path),
        "--review-receipt", str(review_path),
        "--ledger", str(ledger_path),
        "--receipt-store", str(receipt_store),
        "--append-receipts",
        "--ledger-store", str(ledger_store),
        "--append-ledger",
    )
    assert json.loads(run(*command).stdout)["status"] == "valid"
    assert len(receipt_store.read_text(encoding="utf-8").splitlines()) == 2
    assert len(ledger_store.read_text(encoding="utf-8").splitlines()) == 1

    # Re-running an identical completed handoff is idempotent.
    assert json.loads(run(*command).stdout)["status"] == "valid"
    assert len(receipt_store.read_text(encoding="utf-8").splitlines()) == 2
    assert len(ledger_store.read_text(encoding="utf-8").splitlines()) == 1

    # A post-review scoped mutation invalidates the immutable handoff.
    (repo / "app.py").write_text("print('tampered')\n", encoding="utf-8")
    rejected = run(*command, check=False)
    assert rejected.returncode == 2
    assert "current worktree fingerprint" in rejected.stderr
