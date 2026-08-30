from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from decision_protocol import (  # noqa: E402
    DecisionError,
    append_decision_records,
    validate_decision_record,
)
from qwen_local import (  # noqa: E402
    QwenError,
    make_receipt,
    pre_dispatch,
    probe_capabilities,
    validate_write_gate,
)
from validate_config import ConfigError, load_config  # noqa: E402
from validate_handoff import git_worktree_fingerprint, validate_handoff  # noqa: E402

V2 = (Path(__file__).parent.parent / "config" / "example.toml").read_text(encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def v2_records(tmp_path: Path) -> tuple[object, dict, dict, dict]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Phase 1 Test")
    (repo / "app.py").write_text("baseline\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "baseline")
    config_path = tmp_path / ".codex-orchestration.toml"
    config_path.write_text(V2, encoding="utf-8")
    config = load_config(config_path)
    scope = ["app.py"]
    before = git_worktree_fingerprint(str(repo), scope)
    full_before = git_worktree_fingerprint(str(repo), ["."])
    (repo / "app.py").write_text("implemented\n", encoding="utf-8")
    after = git_worktree_fingerprint(str(repo), scope)
    full_after = git_worktree_fingerprint(str(repo), ["."])
    profiles = {
        "technical_authority": {"role_instance_id": "terra-ri", "session_id": "terra-s", "host_id": "terra-h", "context_id": "terra-c", "provider": "codex", "model": "gpt-5.6-terra", "reasoning_effort": "high", "host_identity": "terra-host", "resolution_source": "test", "profile_digest": "1" * 64},
        "orchestrator": {"role_instance_id": "luna-ri", "session_id": "luna-s", "host_id": "luna-h", "context_id": "luna-c", "provider": "codex", "model": "gpt-5.6-luna", "reasoning_effort": "high", "host_identity": "luna-host", "resolution_source": "test", "profile_digest": "2" * 64},
        "luna_worker": {"role_instance_id": "lw-ri", "session_id": "lw-s", "host_id": "lw-h", "context_id": "lw-c", "provider": "codex", "model": "gpt-5.6-luna", "reasoning_effort": "high", "host_identity": "lw-host", "resolution_source": "test", "profile_digest": "3" * 64},
        "claude_worker": {"role_instance_id": "cw-ri", "session_id": "cw-s", "host_id": "cw-h", "context_id": "cw-c", "provider": "claude_code", "model": "opus", "reasoning_effort": "high", "host_identity": "cw-host", "resolution_source": "test", "profile_digest": "4" * 64},
    }
    bindings = {
        role: {
            "role": role,
            **{key: profile[key] for key in ("role_instance_id", "session_id", "host_id", "context_id")},
            "resolved_profile": {key: profile[key] for key in ("provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest")},
        }
        for role, profile in profiles.items()
    }
    orchestrator_profile = {key: profiles["orchestrator"][key] for key in ("provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest")}
    common = {"schema_version": 2, "task_id": "task-v2", "milestone_id": "m1", "milestone_index": 1, "round": 0, "worktree": str(repo), "baseline": {"head_commit": git(repo, "rev-parse", "HEAD"), "dirty_state_fingerprint": before}, "path_scope": scope, "policy_digest": config.policy_digest, "resolved_orchestrator_profile": orchestrator_profile, "role_instance_id": "lw-ri", "session_id": "lw-s", "host_id": "lw-h", "context_id": "lw-c", "resolved_profile": {key: profiles["luna_worker"][key] for key in ("provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest")}, "full_worktree_fingerprint_before": full_before, "full_worktree_fingerprint_after": full_after}
    implementation = {**common, "invocation_id": "impl-v2", "invocation_kind": "implementation", "pair_member": "luna_worker", "provider": "codex", "model": "gpt-5.6-luna", "role": "implementer", "target_fingerprint_before": before, "target_fingerprint_after": after, "read_only": False, "outcome": "success"}
    review = {**common, "invocation_id": "review-v2", "invocation_kind": "review", "pair_member": "claude_worker", "provider": "claude_code", "model": "opus", "role": "reviewer", "role_instance_id": "cw-ri", "session_id": "cw-s", "host_id": "cw-h", "context_id": "cw-c", "resolved_profile": {key: profiles["claude_worker"][key] for key in ("provider", "model", "reasoning_effort", "host_identity", "resolution_source", "profile_digest")}, "target_fingerprint_before": after, "target_fingerprint_after": after, "full_worktree_fingerprint_before": full_after, "full_worktree_fingerprint_after": full_after, "read_only": True, "outcome": "approve"}
    ledger_common = {key: common[key] for key in ("schema_version", "task_id", "milestone_id", "milestone_index", "round", "worktree", "baseline", "path_scope", "policy_digest", "resolved_orchestrator_profile")}
    ledger = {**ledger_common, "runtime_bindings": bindings, "round_kind": "initial", "invocation_count": 2, "max_invocations": 8, "max_correction_rounds": 20, "max_correction_reviews_per_defect": 3, "implementation_target_fingerprint": after, "review_target_fingerprint_before": after, "review_target_fingerprint_after": after, "implementer": "luna_worker", "reviewer": "claude_worker", "implementation_invocation_id": "impl-v2", "review_invocation_id": "review-v2", "correction_review_counts": {}, "status": "reviewed"}
    return config, implementation, review, ledger


def test_v2_requires_fixed_roles_and_preserves_v1(tmp_path: Path) -> None:
    config, *_ = v2_records(tmp_path)
    assert config.policy["technical_authority"]["model"].endswith("terra")
    assert {member["name"] for member in config.policy["pair"]} == {"luna_worker", "claude_worker"}
    assert load_config(tmp_path / ".codex-orchestration.toml").policy["version"] == 2

    bad = V2.replace('name = "claude_worker"', 'name = "other_worker"')
    (tmp_path / "bad.toml").write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(tmp_path / "bad.toml")
    assert error.value.code == "E_PAIR_ROLE"


def test_v2_handoff_rejects_runtime_identity_reuse_and_full_tree_tamper(tmp_path: Path) -> None:
    config, implementation, review, ledger = v2_records(tmp_path)
    validate_handoff(config, implementation, review, ledger)
    reused = copy.deepcopy(ledger)
    reused["runtime_bindings"]["claude_worker"]["session_id"] = "lw-s"
    with pytest.raises(Exception) as error:
        validate_handoff(config, implementation, review, reused)
    assert getattr(error.value, "code", None) == "E_RUNTIME_IDENTITY_REUSE"

    config, implementation, review, ledger = v2_records(tmp_path / "tamper")
    (Path(ledger["worktree"]) / "outside.txt").write_text("reviewer mutation\n", encoding="utf-8")
    with pytest.raises(Exception) as error:
        validate_handoff(config, implementation, review, ledger)
    assert "full-worktree" in str(error.value)


def profile_bindings() -> dict:
    fields = {"technical_authority": ("codex", "terra"), "orchestrator": ("codex", "luna"), "luna_worker": ("codex", "worker"), "claude_worker": ("claude_code", "claude")}
    result = {}
    for number, (role, (provider, model)) in enumerate(fields.items(), 1):
        result[role] = {"role_instance_id": f"ri-{number}", "session_id": f"session-{number}", "host_id": f"host-{number}", "context_id": f"context-{number}", "provider": provider, "model": "gpt-5.6-terra" if role == "technical_authority" else ("gpt-5.6-luna" if role in {"orchestrator", "luna_worker"} else "opus"), "reasoning_effort": "high", "host_identity": f"host-{number}", "resolution_source": "test", "profile_digest": f"{number:x}" * 64}
    return result


def decision(task: str = "task", milestone: str = "m1") -> tuple[dict, dict]:
    profiles = profile_bindings()
    binding = {"profiles": profiles, "worktree": "/tmp/project", "path_scope": ["src"], "target_fingerprint": "a" * 64, "policy_digest": "b" * 64}
    request = {"schema_version": 2, "record_type": "request", "request_id": "req-1", "idempotency_key": "request-key", "task_id": task, "milestone_id": milestone, "milestone_index": 1, "requested_by": "luna_worker", "authority_role": "technical_authority", "question": "May the draft be adopted?", "reason": "The assistant suggested a mechanical patch.", "binding": binding, "status": "pending", "supersedes_request_id": None}
    record = {"schema_version": 2, "record_type": "decision", "decision_id": "dec-1", "idempotency_key": "decision-key", "request_id": "req-1", "task_id": task, "milestone_id": milestone, "milestone_index": 1, "decided_by": "terra", "authority_role": "technical_authority", "authority_available": True, "decision": "accept", "rationale": "All gates passed.", "binding": binding, "status": "decided"}
    return request, record


def test_decision_jsonl_is_idempotent_and_requires_terra(tmp_path: Path) -> None:
    request, record = decision()
    store = tmp_path / "decisions.jsonl"
    append_decision_records(store, [request, record])
    first = store.read_text()
    append_decision_records(store, [request, record])
    assert store.read_text() == first
    unavailable = copy.deepcopy(record)
    unavailable["authority_available"] = False
    with pytest.raises(DecisionError) as error:
        validate_decision_record(unavailable)
    assert error.value.code == "E_DECISION_AUTHORITY_UNAVAILABLE"
    conflict = copy.deepcopy(request)
    conflict["question"] = "different"
    with pytest.raises(DecisionError) as error:
        append_decision_records(store, [conflict])
    assert error.value.code == "E_DECISION_IDEMPOTENCY_CONFLICT"


def test_qwen_is_disabled_untrusted_and_write_gated(tmp_path: Path) -> None:
    config, *_ = v2_records(tmp_path)
    with pytest.raises(QwenError) as error:
        pre_dispatch(config, prompt="hello")
    assert error.value.code == "E_QWEN_DISABLED"
    receipt = make_receipt(invocation_id="q1", task_id="t", milestone_id="m", binding={"role_instance_id": "q-ri", "session_id": "q-s", "host_id": "q-h", "context_id": "q-c"}, endpoint="https://127.0.0.1:8000/v1", model="Qwen3.8-27B", prompt="hello", output="suggestion", input_tokens=1, output_tokens=1)
    assert receipt["untrusted"] is True and receipt["read_only"] is True
    qwen = config.policy["qwen_local"]
    with pytest.raises(QwenError) as error:
        validate_write_gate(qwen, {"mode": "draft_patch"})
    assert error.value.code == "E_QWEN_WRITE_GATE"
    with pytest.raises(QwenError) as error:
        probe_capabilities(config)
    assert error.value.code == "E_QWEN_DISABLED"
