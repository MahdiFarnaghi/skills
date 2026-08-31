# Continuous orchestration contract

Luna owns the host-native dispatch and wait calls; `orchestration_state.py`
owns durable state transitions. Persist a dispatch intent and idempotency key
before dispatch, persist the returned host job id immediately after dispatch,
and reconcile that job before retrying after a crash or timeout.

The state engine permits only this automatic routing:

```text
planned -> implementation_running -> implementation_validating
  -> review_running -> review_validating
  -> milestone_verifying -> awaiting_terra_decision
  -> milestone_complete -> planned (next milestone)

review_validating [needs_correction]
  -> needs_correction -> correction_running -> correction_validating
  -> rereview_running -> rereview_validating
```

Worker failures return to the same phase's dispatch state while failure and
invocation budgets remain. They never change ownership. Approvals, corrections,
and failures are persisted before the next host side effect. Terminal states
are `complete`, `blocked`, `escalated`, and `cancelled`; no worker completion
or routine retry is terminal.

Fingerprints and authoritative verification are Luna responsibilities. A
reviewer without shell access may review the exact frozen snapshot and Luna's
evidence bundle, but must declare unavailable checks. A reviewer without exact
snapshot access has no verdict and requires an independent fallback or
escalation.
