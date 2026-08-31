#!/usr/bin/env python3
"""Dependency-free Phase 3 state-machine integration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestration_state import OrchestrationStore, StateError


class Phase3StateMachineTests(unittest.TestCase):
    def store(self, **limits):
        directory = Path(tempfile.mkdtemp())
        store = OrchestrationStore(directory)
        store.create(task_id="t1", milestone_id="m1", limits=limits or None)
        return store

    def dispatch(self, store, phase, job, invocation):
        return store.transition("dispatch", phase=phase, job_id=job, invocation_id=invocation)

    def test_automatic_implementation_review_correction_rereview_and_acceptance(self):
        store = self.store()
        self.assertEqual(store.read()["next_action"]["phase"], "implementation")
        self.dispatch(store, "implementation", "job-i", "inv-i")
        store.transition("worker_complete", receipt_id="receipt-i", outcome="success")
        store.transition("receipt_validated", outcome="success", receipt_id="receipt-i")
        self.assertEqual(store.read()["state"], "review_dispatching")
        self.dispatch(store, "review", "job-r", "inv-r")
        store.transition("worker_complete", receipt_id="receipt-r", outcome="needs_correction")
        store.transition("receipt_validated", outcome="needs_correction", finding_ids=["F1"])
        self.assertEqual(store.read()["next_action"]["phase"], "correction")
        self.dispatch(store, "correction", "job-c", "inv-c")
        store.transition("worker_complete", receipt_id="receipt-c", outcome="success")
        store.transition("receipt_validated", outcome="success", receipt_id="receipt-c")
        self.dispatch(store, "rereview", "job-rr", "inv-rr")
        store.transition("worker_complete", receipt_id="receipt-rr", outcome="approve")
        store.transition("receipt_validated", outcome="approve", receipt_id="receipt-rr")
        store.transition("verification_passed")
        store.transition("terra_accepted")
        self.assertEqual(store.read()["state"], "milestone_complete")
        self.assertEqual(store.read()["next_action"]["kind"], "advance_or_complete")

    def test_failures_retry_same_phase_and_count_once(self):
        store = self.store(max_failed_invocations=1, max_invocations=48)
        self.dispatch(store, "implementation", "job-1", "inv-1")
        store.transition("worker_failed", reason="timeout")
        self.assertEqual(store.read()["state"], "planned")
        self.assertEqual(store.read()["failed_invocations"], 1)
        self.dispatch(store, "implementation", "job-2", "inv-2")
        store.transition("worker_failed", reason="transport")
        self.assertEqual(store.read()["state"], "escalated")

    def test_duplicate_event_is_idempotent_and_limits_are_fail_closed(self):
        store = self.store(max_correction_rounds=0, max_failed_invocations=6, max_invocations=48)
        first = self.dispatch(store, "implementation", "job-1", "inv-1")
        duplicate = store.transition("dispatch", phase="implementation", job_id="job-1", invocation_id="inv-1")
        self.assertEqual(first, duplicate)
        store.transition("worker_complete", receipt_id="receipt-i", outcome="success")
        store.transition("receipt_validated", outcome="success", receipt_id="receipt-i")
        self.dispatch(store, "review", "job-r", "inv-r")
        store.transition("worker_complete", receipt_id="receipt-r", outcome="needs_correction")
        store.transition("receipt_validated", outcome="needs_correction", finding_ids=["F1"])
        self.dispatch(store, "correction", "job-c", "inv-c")
        store.transition("worker_complete", receipt_id="receipt-c", outcome="success")
        self.assertEqual(store.transition("receipt_validated", outcome="success", receipt_id="receipt-c")["state"], "escalated")


if __name__ == "__main__":
    unittest.main()
