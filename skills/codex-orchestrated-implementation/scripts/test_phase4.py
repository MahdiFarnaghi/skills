#!/usr/bin/env python3
"""Deterministic host-supervisor tests; no model or host API is used."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from continuation_guard import ContinuationGuard, ContinuationSupervisor, GuardError, validate_host_action, validate_host_result
from orchestration_state import OrchestrationStore


def new_guard() -> tuple[ContinuationGuard, Path]:
    path = Path(tempfile.mkdtemp())
    store = OrchestrationStore(path)
    profile = lambda provider, model, host: {"provider": provider, "model": model, "reasoning_effort": "low", "host_identity": host, "resolution_source": "test", "profile_digest": "d" * 64}
    store.create(
        task_id="task", milestone_id="m1", worktree=str(path), path_scope=["src"], receipt_location="receipt.json",
        policy_digest="a" * 64,
        resolved_orchestrator_profile=profile("codex", "gpt-5.6-luna", "orchestrator-host"),
        runtime_bindings={
            "luna_worker": {"role_instance_id": "luna-ri", "session_id": "luna-s", "host_id": "luna-h", "context_id": "luna-c", "resolved_profile": profile("codex", "gpt-5.6-luna", "luna-host")},
            "claude_worker": {"role_instance_id": "claude-ri", "session_id": "claude-s", "host_id": "claude-h", "context_id": "claude-c", "resolved_profile": profile("claude_code", "opus", "claude-host")},
        },
    )
    return ContinuationGuard(store, lease_duration_seconds=120), path


class FakeHost:
    def __init__(self):
        self.jobs: dict[str, list[dict]] = {}
        self.dispatches: list[str] = []
        self.waits = 0
        self.reviewed = False
        self.corrections = 0
        self.actions: dict[str, dict] = {}

    @staticmethod
    def result(job: str, status: str, *, cursor: str | None = None, progress=None, receipt: str | None = None):
        return {"host_job_id": job, "status": status, "cursor": cursor, "progress": progress, "completion_reason": "", "receipt_reference": receipt, "user_attention_required": False}

    def dispatch(self, action):
        job = f"{action['phase']}-{len(self.dispatches)}"
        self.dispatches.append(action["phase"])
        self.actions[job] = {**action, "job_id": job, "receipt_reference": job}
        self.jobs[job] = [self.result(job, "running", cursor="1", progress={"message": "working"}), self.result(job, "completed", cursor="2", progress={"outcome": "needs_correction" if action["phase"] == "review" else "approve" if action["phase"] == "rereview" else "success"}, receipt=job)]
        return self.result(job, "queued", cursor="0", progress={})

    def wait(self, action):
        self.waits += 1
        return self.jobs[action["job_id"]].pop(0)

    def receipt(self, action):
        job = action["job_id"]
        contract = self.actions[job]
        outcome = "needs_correction" if job.startswith("review") else "approve" if job.startswith("rereview") else "success"
        return {
            "schema_version": 2, "task_id": contract["task_id"], "milestone_id": contract["milestone_id"],
            "milestone_index": contract["milestone_index"], "round": contract["round"], "invocation_id": contract["invocation_id"],
            "invocation_kind": "review" if contract["phase"] in {"review", "rereview"} else "implementation",
            "pair_member": contract["role"], "provider": contract["provider"], "model": contract["resolved_profile"]["model"],
            "role": "reviewer" if contract["phase"] in {"review", "rereview"} else "implementer", "worktree": contract["worktree"],
            "baseline": {"head_commit": "1" * 40, "dirty_state_fingerprint": "2" * 64}, "path_scope": contract["path_scope"],
            "target_fingerprint_before": "3" * 64, "target_fingerprint_after": "3" * 64,
            "full_worktree_fingerprint_before": "4" * 64, "full_worktree_fingerprint_after": "4" * 64,
            "read_only": contract["phase"] in {"review", "rereview"}, "policy_digest": contract["policy_digest"],
            "resolved_orchestrator_profile": contract["resolved_orchestrator_profile"], "role_instance_id": contract["role_instance_id"],
            "session_id": contract["session_id"], "host_id": contract["host_id"], "context_id": contract["context_id"],
            "resolved_profile": contract["resolved_profile"], "host_job_id": job, "receipt_reference": job,
            "idempotency_key": contract["idempotency_key"], "finding_ids": ["F1"] if outcome == "needs_correction" else [], "outcome": outcome,
        }

    def validate_receipt(self, receipt, state):
        return {"outcome": receipt["outcome"], "receipt_id": receipt["invocation_id"], "receipt_digest": __import__("continuation_guard").digest(receipt), "finding_ids": receipt["finding_ids"]}

    def verify(self, state):
        return {"passed": True}

    def terra_decision(self, state):
        return {"accepted": True, "complete": True}


def test_same_run_delayed_worker_routes_review_correction_and_terminal():
    guard, _ = new_guard()
    host = FakeHost()
    result = ContinuationSupervisor(guard, host, lease_id="lease", max_steps=100).run()
    assert result["action"] == "final_allowed"
    assert host.waits >= 4
    assert host.dispatches == ["implementation", "review", "correction", "rereview"]
    assert guard.state()["state"] == "complete"
    assert guard.state()["invocation_count"] == 4


def test_running_poll_does_not_increment_invocations_or_finalize():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="t")
    action = guard.next_action(); guard.record_dispatch_intent(action)
    guard.record_dispatch_result(lease_id="l", result={"host_job_id": "j", "status": "queued", "cursor": "0", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    before = guard.state()["invocation_count"]
    guard.record_wait_result(lease_id="l", result={"host_job_id": "j", "status": "running", "cursor": "1", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    assert guard.state()["invocation_count"] == before
    with pytest.raises(GuardError) as error:
        guard.assert_finalizable(lease_id="l")
    assert error.value.code == "E_FINAL_NOT_ALLOWED"
    assert guard.state()["audit"][-1]["type"] == "premature_final_attempt"


def test_unknown_action_and_result_fields_fail_closed():
    with pytest.raises(GuardError):
        validate_host_action({"action": "dispatch_codex", "unexpected": True})
    with pytest.raises(GuardError):
        validate_host_result({"host_job_id": "j", "status": "done", "cursor": None, "progress": None, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})


def test_lease_prevents_concurrent_owner_and_released_lease_is_required():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l1", owner_role_instance_id="o1", host_thread_id="t")
    with pytest.raises(GuardError) as error:
        guard.acquire_lease(lease_id="l2", owner_role_instance_id="o2", host_thread_id="t")
    assert error.value.code == "E_LEASE_HELD"


def test_terminal_unresolved_job_cannot_finalize():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="t")
    guard.store.transition("dispatch", phase="implementation", job_id="j", invocation_id="i")
    guard.store.transition("block", reason="user")
    with pytest.raises(GuardError):
        guard.assert_finalizable(lease_id="l")


def test_receipt_replay_is_rejected():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="t")
    host = FakeHost()
    action = guard.next_action(); guard.record_dispatch_intent(action)
    queued = host.dispatch(action)
    guard.record_dispatch_result(lease_id="l", result=queued)
    guard.record_wait_result(lease_id="l", result=host.result("implementation-0", "completed", cursor="1", progress={"outcome": "success"}, receipt="implementation-0"))
    validate_action = guard.next_action()
    forged = {"invocation_id": "evil", "outcome": "success"}
    before = guard.state()
    with pytest.raises(GuardError) as forged_error:
        guard.record_receipt(lease_id="l", reference="implementation-0", receipt=forged)
    assert forged_error.value.code == "E_RECEIPT_CONTRACT"
    assert guard.state() == before
    receipt = host.receipt(validate_action)
    guard.record_receipt(lease_id="l", reference="implementation-0", receipt=receipt)
    guard.validate_receipt(lease_id="l", outcome="success", receipt_id=receipt["invocation_id"], receipt_digest=__import__("continuation_guard").digest(receipt), finding_ids=[])
    with pytest.raises(GuardError) as error:
        guard.validate_receipt(lease_id="l", outcome="success", receipt_id=receipt["invocation_id"])
    assert error.value.code == "E_RECEIPT_DUPLICATE"


def test_unknown_dispatch_keeps_intent_and_exhausts_reconciliation_budget():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="t")
    action = guard.next_action(); guard.record_dispatch_intent(action)
    unknown = {"host_job_id": "lost", "status": "unknown", "cursor": "c0", "progress": {}, "completion_reason": "lost response", "receipt_reference": None, "user_attention_required": False}
    guard.record_dispatch_result(lease_id="l", result=unknown)
    assert guard.state()["pending_dispatch"]["idempotency_key"] == action["idempotency_key"]
    assert guard.next_action()["action"] == "reconcile_job"
    guard.reconcile(lease_id="l", result=unknown)
    guard.reconcile(lease_id="l", result=unknown)
    guard.reconcile(lease_id="l", result=unknown)
    assert guard.state()["state"] == "escalated"
    assert guard.state()["pending_dispatch"] is None


def test_expired_lease_adopts_existing_job_before_waiting():
    guard, path = new_guard()
    clock = [100.0]
    store = guard.store
    expiring = ContinuationGuard(store, clock=lambda: clock[0], lease_duration_seconds=1)
    expiring.acquire_lease(lease_id="old", owner_role_instance_id="old-owner", host_thread_id="thread")
    action = expiring.next_action(); expiring.record_dispatch_intent(action)
    expiring.record_dispatch_result(lease_id="old", result={"host_job_id": "job", "status": "queued", "cursor": "c0", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    clock[0] = 102.0
    adopted = ContinuationGuard(store, clock=lambda: clock[0], lease_duration_seconds=10)
    adopted.acquire_lease(lease_id="new", owner_role_instance_id="new-owner", host_thread_id="thread")
    assert adopted.state()["continuation_lease"]["status"] == "handoff_pending"
    assert adopted.next_action()["action"] == "reconcile_job"
    with pytest.raises(GuardError) as heartbeat_error:
        adopted.heartbeat(lease_id="new")
    assert heartbeat_error.value.code == "E_LEASE_HANDOFF_REQUIRED"
    with pytest.raises(GuardError) as old_owner_error:
        expiring.heartbeat(lease_id="old")
    assert old_owner_error.value.code == "E_LEASE_OWNER"
    adopted.reconcile(lease_id="new", result={"host_job_id": "job", "status": "running", "cursor": "c1", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    assert adopted.state()["continuation_lease"]["status"] == "active"
    assert adopted.state()["continuation_lease"]["generation"] == 2
    assert adopted.next_action()["action"] == "wait_codex"


def test_wakeup_binding_and_callback_reconcile_same_job():
    guard, _ = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="thread")
    action = guard.next_action(); guard.record_dispatch_intent(action)
    guard.record_dispatch_result(lease_id="l", result={"host_job_id": "job", "status": "queued", "cursor": "c0", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    guard.record_wait_result(lease_id="l", result={"host_job_id": "job", "status": "running", "cursor": "c1", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    guard.store.transition("wakeup_needed", reason="forced suspension")
    lease = guard.state()["continuation_lease"]
    wakeup = {"wakeup_id": "w1", "thread_id": "thread", "task_id": "task", "milestone_id": "m1", "state_generation": lease["generation"], "lease_id": "l", "active_job_id": "job", "wait_cursor": "c1", "earliest_poll_at": "2026-08-31T00:00:00Z", "hard_deadline": "2026-09-01T00:00:00Z", "resume_instruction": "reconcile the existing job and continue", "idempotency_key": guard._wakeup_key(guard.state(), lease)}
    forged = {**wakeup, "task_id": "other-task"}
    with pytest.raises(GuardError) as binding_error:
        guard.record_wakeup(lease_id="l", wakeup=forged)
    assert binding_error.value.code == "E_WAKEUP_BINDING"
    assert guard.state()["wakeup"] is None
    guard.record_wakeup(lease_id="l", wakeup=wakeup)
    guard.resume_wakeup(lease_id="l", wakeup_id="w1", result={"host_job_id": "job", "status": "running", "cursor": "c2", "progress": {}, "completion_reason": "", "receipt_reference": None, "user_attention_required": False})
    assert guard.state()["wakeup"] is None
    assert guard.state()["continuation_lease"]["wait_cursor"] == "c2"


def test_restart_reconstructs_reconciliation_and_receipt_boundaries():
    guard, path = new_guard()
    guard.acquire_lease(lease_id="l", owner_role_instance_id="o", host_thread_id="t")
    action = guard.next_action(); guard.record_dispatch_intent(action)
    unknown = {"host_job_id": "lost", "status": "unknown", "cursor": "c0", "progress": {}, "completion_reason": "lost response", "receipt_reference": None, "user_attention_required": False}
    guard.record_dispatch_result(lease_id="l", result=unknown)
    assert guard.store.reconstruct() == guard.state()
