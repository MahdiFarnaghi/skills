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

## Host supervisor and lease

`continuation_guard.py` is the closed boundary between the deterministic state
engine and host-native Codex/Claude tools. It emits one action at a time from
the allowlist in `schemas/host-action.schema.json`; Luna performs that action
and submits a result conforming to `schemas/host-result.schema.json`.

| Source | Event/guard | Counter effect | Side effect | Next action/state |
| --- | --- | --- | --- | --- |
| `planned` or `*_dispatching` | valid dispatch intent, then host confirmation | one invocation only after confirmation | persist intent before dispatch and host job id after it | bounded wait in `*_running` |
| `*_running` | `queued`/`running`/`unknown` result | none | update cursor and heartbeat | same wait; unknown requires reconciliation |
| `*_running` | completed result | none | fetch receipt immediately | `validate_receipt` |
| `*_running` | failed/cancelled result | one failed invocation | retry same owner within limits | same phase dispatch or `escalated` |
| `*_validating` | valid receipt | none | consume receipt digest exactly once | review, correction, verification, or escalation |
| `milestone_verifying` | trusted checks pass | none | record immutable evidence | Terra decision |
| `awaiting_terra_decision` | accept/reject | none | append decision record | next milestone, correction, or terminal |
| terminal | all side effects resolved and lease released | none | finalization audit | `final_allowed` |

Polling is not an invocation. A wait timeout, empty progress, missing final
text, or transport return is `running` or `unknown`, never completion. The
supervisor repeats waits in windows no longer than 60 seconds. Hard and
progress timeouts produce a failed/no-verdict event only after the persisted
job is queried and proven terminal. Receipt-only recovery uses the same host
job and does not consume a worker retry.

The continuation lease is acquired before the first dispatch and refreshed
after dispatch, every wait, receipt consumption, and transition. Its generation
is compared under the state-store lock. An expired lease does not authorize a
new worker: the new Luna instance adopts the lease only after reconciling the
persisted job id and appending a handoff event. A pending dispatch intent is
also reconciled by idempotency key before any retry. Wakeups are thread-bound,
idempotent, and carry the task, milestone, generation, lease, job, cursor,
poll time, hard deadline, and resume instruction.

## Finalization

`assert-finalizable` appends `premature_final_attempt` and exits nonzero for
every non-terminal state. It also fails for an active or unknown job, a
pending dispatch intent, an unconsumed receipt, a pending Terra decision, an
active wakeup, or an unreleased lease. Only after all of these are clear does
it append an allowed finalization audit and return `final_allowed`. A forced
host suspension registers one wakeup before yielding; the callback resumes
the same task, adopts/reconciles the lease, and returns to the loop without a
user message.

`scripts/real_host_canary.py` is the opt-in host integration entry point. It
does not impersonate either worker: CI supplies a tracked Codex/Claude harness
with credentials and enables `CODEX_ORCHESTRATION_REAL_HOST_CANARY=1`. The
harness must report `status = passed`, a disposable absolute `worktree`, an
ordered `events` trace, zero `user_wakeup_count` and `user_message_count`,
plus each of implementation, review, correction, re-review, verification, and
Terra acceptance with a host job id, at least one dispatch and wait, and a
consumed receipt. Local runs print a visible skip reason because bundled unit
tests cannot prove that the host keeps one turn alive.
