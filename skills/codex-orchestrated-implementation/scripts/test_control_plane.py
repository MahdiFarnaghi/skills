from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from decision_protocol import DecisionError, append_decision_records  # noqa: E402
from qwen_local import QwenError, make_receipt, pre_dispatch, validate_write_gate  # noqa: E402
from validate_config import load_config  # noqa: E402


ROOT = Path(__file__).parents[1]


def test_v2_example_has_distinct_control_plane_and_qwen_policy() -> None:
    resolved = load_config(ROOT / "config" / "example.toml")
    assert resolved.policy["technical_authority"]["model"].endswith("-terra")
    assert resolved.policy["orchestrator"]["model"].endswith("-luna")
    assert {m["name"] for m in resolved.policy["pair"]} == {"luna_worker", "claude_worker"}
    assert resolved.policy["qwen_local"]["write_mode"] == "disabled"


def test_qwen_is_fail_closed_and_receipt_is_untrusted() -> None:
    resolved = load_config(ROOT / "config" / "example.toml")
    with pytest.raises(QwenError) as error:
        pre_dispatch(resolved, prompt="map the repository")
    assert error.value.code == "E_QWEN_DISABLED"
    digest = "a" * 64
    receipt = make_receipt(
        invocation_id="q1", task_id="t1", milestone_id="m1",
        binding={"role_instance_id": "qwen-1", "session_id": "s1", "host_id": "h1", "context_id": "c1"},
        endpoint="https://127.0.0.1:8000/v1", model="Qwen3.8-27B",
        prompt="x", output="y", input_tokens=1, output_tokens=1,
    )
    assert receipt["untrusted"] is True
    assert hashlib.sha256(b"x").hexdigest() == receipt["request_digest"]
    assert digest != receipt["request_digest"]


def test_qwen_write_gate_requires_terra_and_pair_review() -> None:
    qwen = {"write_mode": "draft_patch"}
    base = {
        "mode": "draft_patch", "terra_decision": "accept", "integration_tests_passed": True,
        "adopted_by": "luna_worker", "opposite_pair_reviewed": True,
        "reviewed_by": "claude_worker", "isolated_worktree": "/tmp/qwen-draft",
        "direct_worktree_mutation": False,
    }
    validate_write_gate(qwen, base)
    with pytest.raises(QwenError):
        validate_write_gate(qwen, {**base, "reviewed_by": "luna_worker"})


def test_decision_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    digest = "b" * 64
    profile = {
        "provider": "codex", "model": "gpt-5.6-luna", "reasoning_effort": "high",
        "host_identity": "host", "resolution_source": "runtime", "profile_digest": digest,
        "role_instance_id": "instance", "session_id": "session", "host_id": "host", "context_id": "context",
    }
    profiles = {role: {**profile, "role_instance_id": f"{role}-i", "session_id": f"{role}-s", "host_id": f"{role}-h", "context_id": f"{role}-c"} for role in ("technical_authority", "orchestrator", "luna_worker", "claude_worker")}
    binding = {"profiles": profiles, "worktree": "/tmp/project", "path_scope": ["src"], "target_fingerprint": digest, "policy_digest": digest}
    request = {"schema_version": 2, "record_type": "request", "request_id": "r1", "idempotency_key": "k1", "task_id": "t1", "milestone_id": "m1", "milestone_index": 1, "requested_by": "luna", "authority_role": "technical_authority", "question": "accept?", "reason": "test", "binding": binding, "status": "pending", "supersedes_request_id": None}
    store = tmp_path / "decisions.jsonl"
    append_decision_records(store, [request, request])
    assert len(store.read_text().splitlines()) == 1
    with pytest.raises(DecisionError) as error:
        append_decision_records(store, [{**request, "question": "different"}])
    assert error.value.code == "E_DECISION_IDEMPOTENCY_CONFLICT"
