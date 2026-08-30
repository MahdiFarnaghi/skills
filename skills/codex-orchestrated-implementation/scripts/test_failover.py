from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parent))
from failover import FailoverError, append_transition, validate_transition
from validate_config import load_config

ROOT = Path(__file__).parents[1]

def transition():
    return {"schema_version": 2, "record_type": "role_transition", "transition_id": "tr-1", "idempotency_key": "tr-key", "task_id": "task", "milestone_id": "m1", "milestone_index": 1, "failed_role": "claude_worker", "replacement_role": "luna_worker", "transition_kind": "reviewer_fallback", "failure_reason": "quota_exhausted", "failed_invocation_id": "claude-review-1", "terra_decision_id": "terra-decision-1", "approved_by": "terra", "replacement_role_instance_id": "luna-fallback-1", "replacement_session_id": "luna-session-2", "replacement_context_id": "luna-context-2", "path_scope": ["src"], "target_fingerprint": "a" * 64, "status": "approved"}

def test_terra_approved_failover_is_idempotent(tmp_path):
    config = load_config(ROOT / "config" / "example.toml")
    item = transition()
    validate_transition(config, item)
    store = tmp_path / "transitions.jsonl"
    append_transition(str(store), config, item)
    append_transition(str(store), config, item)
    assert len(store.read_text().splitlines()) == 1

def test_failover_requires_terra(tmp_path):
    config = load_config(ROOT / "config" / "example.toml")
    with pytest.raises(FailoverError) as error:
        validate_transition(config, {**transition(), "approved_by": "luna"})
    assert error.value.code == "E_FAILOVER_AUTHORITY"

def test_failover_can_be_disabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text((ROOT / "config" / "example.toml").read_text().replace("enabled = true\nallow_luna_worker_fallback", "enabled = false\nallow_luna_worker_fallback"))
    config = load_config(path)
    with pytest.raises(FailoverError) as error:
        validate_transition(config, transition())
    assert error.value.code == "E_FAILOVER_DISABLED"
