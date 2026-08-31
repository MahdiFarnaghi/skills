# Plan

## Phase 1 — Establish the Terra authority and Luna orchestration architecture

Replace the current single-orchestrator model with a two-level control plane:
Terra is the technical authority; Luna is the execution orchestrator. Add a
separate Luna worker alongside the existing Claude worker. This phase changes
the authority model before adding any new local-model provider.

### Role model

```text
Terra — technical authority and final acceptance
  ↓
Luna — orchestration lead
  ↓
Luna worker / Claude worker
```

- **Terra** approves architecture, scope changes, safety decisions, migrations,
  compatibility changes, worker conflicts, exceptions, and final delivery.
- **Luna orchestrator** decomposes approved work, assigns tasks, manages
  worktrees, receipts, ledgers, handoffs, correction loops, and escalations.
- **Luna worker** and **Claude worker** are separate execution instances that
  perform bounded implementation, testing, investigation, or review work.
- Workers report only to Luna. Luna sends important decisions to Terra through
  a structured decision request; workers never bypass the orchestrator.

### Scope

- Replace the fixed two-member configuration with an explicit role schema:
  `technical_authority`, `orchestrator`, a required reciprocal implementation/
  review pair, and optional constrained assistants reserved for later phases.
- Bind every runtime task to an agent/session identity, model, reasoning effort,
  worktree, scope, and context-isolation identifier. Configuration names roles;
  runtime receipts prove distinct execution instances.
- Add a Terra decision protocol with a decision request, decision record,
  timeout, unavailable-authority behavior, and append-only ledger binding.
- Make reciprocal pairing explicit: Luna worker implements → Claude reviews;
  Claude implements → Luna worker reviews. The review worker remains read-only.
- Preserve immutable Git handoffs, scoped fingerprints, evidence validation,
  correction accounting, and only-original-implementer correction ownership.

### Decision protocol

Luna sends Terra a compact decision request containing the decision id,
milestone, question, evidence, options, recommendation, affected scope,
reversibility, and blocking condition. Terra returns a durable decision record
with the selected option, rationale, conditions, authority identity, and
profile digest.

If Terra is unavailable, Luna may prepare and verify only already-approved,
reversible, low-risk work. It may not declare such work complete, merge it,
deliver it, or proceed with architecture, security, privacy, durable-state,
migration, compatibility, deployment, destructive, or irreversible decisions.

### Acceptance criteria

- Configuration and validation distinguish Terra authority, Luna orchestrator,
  Luna worker, and Claude worker without static implementer/reviewer labels.
- Runtime validation rejects reuse of an agent/session identity for both Luna
  orchestration and Luna worker roles.
- Integration tests prove alternating Luna/Claude implementation and review,
  read-only review enforcement, context isolation, worker-to-Luna reporting,
  and Luna-to-Terra escalation.
- Terra decisions are stored append-only and bound to the task, milestone,
  execution profile, and relevant handoff/ledger state.
- Existing immutable handoff, receipt, ledger, and end-to-end tests remain
  green after the authority-model migration.

## Phase 2 — Add Qwen3.8-27B as a constrained local assistant

Integrate the server-hosted Qwen3.8-27B model as an optional `qwen_local`
assistant under Luna orchestration. Qwen is not part of the reciprocal
Luna/Claude pair and never acts as Terra, Luna, or final acceptance authority.

### Scope

- Add a strict `qwen_local` configuration block and local-server adapter.
  Define the endpoint protocol, TLS/authentication policy, model allowlist,
  capability probe, context/input/output budgets, reasoning setting, timeout,
  and retry behavior.
- Initially permit only compact, lower-risk work: repository mapping, focused
  read-only investigation, prescribed test execution and failure summaries,
  draft documentation, and draft test artifacts.
- Require a compact task packet, exact file/path scope, fixed token budget,
  low or medium reasoning effort, tool/skill autonomy disabled, and a
  structured receipt validated by Luna.
- Treat Qwen output as untrusted evidence. Luna validates every result against
  the current worktree, project policy, receipt schema, and acceptance ledger.

### Write policy

Qwen starts read-only. Enable narrowly scoped mechanical edits only after a
dedicated integration test proves the complete frozen-snapshot path: Qwen edits
an isolated worktree; Luna or Claude reviews the same scoped fingerprint
read-only; Luna records the result; Terra accepts the milestone. Qwen is never
the sole reviewer for an important change.

### Exclusions

- No architecture ownership, task decomposition, worker conflict resolution,
  final review, completion declaration, merge, deployment, or external action.
- No security, privacy, durable-state, migration, concurrency, recovery,
  compatibility, destructive, or irreversible decisions.
- No long, unbounded, transcript-heavy, or autonomous tool-selection sessions.
  Split work into compact packets when the relevant context exceeds the
  configured input budget.

### Acceptance criteria

- Configuration rejects a missing or unreachable server, failed authentication,
  TLS-policy failure, model not present in the project allowlist, unsupported
  Qwen role, or capability/context-budget mismatch with a stable error.
- Every Qwen receipt records endpoint identity, server capability result, model,
  context/input/output limits, reasoning setting, task scope, and timeout.
- Integration tests prove permitted read-only tasks return valid receipts and
  prohibited roles/actions are rejected before dispatch.
- The optional mechanical-write path proves immutable handoff, independent
  Luna/Claude review, and Terra acceptance; it remains disabled unless this
  test passes.
- Terra authority, Luna orchestration, reciprocal pair behavior, and final
  acceptance remain correct when Qwen is unavailable, times out, or fails.

## Phase 3 — Add continuous orchestration and automatic receipt routing

Turn the Phase 1/2 governance model into a durable run-to-terminal-state
workflow. Luna must continue through worker completion, receipt validation,
review, correction, verification, and the next milestone without treating a
worker boundary as permission to end the turn. Preserve all existing Terra
authority, reciprocal ownership, immutable handoff, and read-only review
rules.

### Run-to-terminal-state invariant

Once Luna dispatches the first worker for a task, consider orchestration
active. While orchestration is active, Luna must not emit a final response if
a worker job is running or if the current state has an automatic next action.
Use progress commentary and bounded host-native waits while work continues.

Luna may end active orchestration only when the task reaches one of these
terminal states:

- `complete`: Terra accepted the final verified result.
- `blocked`: progress requires user input, authorization, credentials, or an
  external state change that Luna cannot safely resolve.
- `escalated`: a safety, evidence, independence, timeout, retry, or accounting
  limit requires Terra or user intervention.
- `cancelled`: the user cancelled or replaced the task.

A completed worker invocation, a completed review, a correction request, a
tool timeout, or a routine transport retry is not by itself a terminal state.
When a valid automatic transition exists, execute it immediately.

If the host forces suspension between turns, persist the complete state and
register a host-native wakeup/heartbeat tied to the active job before yielding.
On wakeup, resume from the persisted state without redispatching completed or
active jobs. Detect wait/wakeup capability before the first dispatch. If the
host supports neither a bounded wait nor a durable wakeup for a job that can
outlive the turn, stop before dispatch and report a capability blocker; do not
claim continuous execution.

### Durable orchestration state

Add `scripts/orchestration_state.py` as the deterministic state and transition
engine. Keep worker dispatch and waiting in Luna through approved host-native
Codex and Claude mechanisms; do not launch unmanaged agent processes from the
script. The script must expose idempotent operations to create a task, register
a dispatch, record progress, ingest a receipt, record validation, select the
next action, mark a timeout/failure, and enter a terminal state.

Persist append-only events plus one reconstructable current-state projection.
Add `schemas/orchestration-event.schema.json` and
`schemas/orchestration-state.schema.json`. Every state must bind:

- task, milestone, round, and invocation identifiers;
- current state and previous state;
- writer and independent reviewer ownership;
- active worker provider, runtime identity, host job id, and resume cursor;
- absolute worktree, exact path scope, baseline identity, and current frozen
  fingerprint;
- policy and resolved execution-profile digests;
- receipt and decision-record digests already consumed;
- total, failed, retry, correction-round, and per-finding review counters;
- dispatch, heartbeat, progress, timeout, and transition timestamps;
- the selected next action and any terminal reason.

Use canonical-content idempotency. Replaying an identical event is a no-op;
reusing an event, invocation, receipt, or host job id with conflicting content
must fail closed. Reconstructing state from the append-only event log must
produce the same projection. Use an atomic lock or equivalent compare-and-set
guard so two resumed Luna executions cannot advance the same task
concurrently.

### State machine and automatic routing

Implement these non-terminal states:

```text
planned
implementation_dispatching
implementation_running
implementation_validating
review_dispatching
review_running
review_validating
needs_correction
correction_dispatching
correction_running
correction_validating
rereview_dispatching
rereview_running
rereview_validating
milestone_verifying
awaiting_terra_decision
milestone_complete
advancing_milestone
```

Enforce the following transitions:

1. `planned` dispatches the configured milestone writer and enters
   `implementation_running` only after persisting the host job id.
2. A running state repeatedly uses the host-native bounded wait. Progress
   updates refresh the heartbeat but do not create worker invocations.
3. Successful implementation enters `implementation_validating`. Luna
   validates the receipt, commands, changed paths, and current worktree,
   computes the scoped and full-worktree handoff fingerprints, freezes the
   snapshot, and immediately dispatches the opposite pair member for review.
4. Review completion enters `review_validating`. Luna recomputes the scoped
   and full-worktree fingerprints, proves review immutability, validates the
   receipt and findings, and routes the result without yielding a final
   response.
5. `needs_correction` routes accepted findings only to the milestone's
   original writer. It must never transfer correction ownership because of a
   retry or timeout. After correction validation, freeze the new snapshot and
   dispatch the opposite pair member for `rereview_running`.
6. `approve` enters `milestone_verifying`. Luna runs the required focused and
   applicable full checks against the approved snapshot and records their
   evidence. Passing review does not bypass orchestrator verification.
7. After verification, enter `awaiting_terra_decision` and use the existing
   append-only Terra decision protocol. Terra acceptance advances to the next
   milestone or `complete`; rejection or conditions route to the applicable
   correction or replanning state.
8. `milestone_complete` immediately enters `advancing_milestone` when an
   approved milestone remains. Derive the next alternating writer/reviewer
   assignment, persist it, and dispatch the next writer.
9. Any no-verdict failure follows the retry/failover rules below. It must not
   be interpreted as `approve`, `needs_correction`, or completion.

Add `references/continuous-orchestration.md` with the complete transition
table. For each source state, specify allowed events, validation guards,
counter changes, side effects, next state, and terminal/error behavior. Keep
the compact invariant and routing summary in `SKILL.md`; route implementation
details to the reference.

### Waiting, progress, timeout, and retry policy

Add a required `[continuation]` block to the v2 configuration and example:

```toml
[continuation]
poll_interval_seconds = 60
progress_timeout_seconds = 600
implementation_timeout_seconds = 3600
review_timeout_seconds = 2400
max_transport_retries = 4
max_worker_retries = 2
receipt_recovery_retries = 1
max_failed_invocations = 6
require_durable_wakeup = true
```

Validate positive bounded values and reject unknown fields. Define the
semantics precisely:

- A poll or wait is not an invocation and does not alter ownership.
- A progress timeout means no heartbeat or observable job progress within the
  configured interval. A hard timeout means total execution exceeded the
  implementation or review limit.
- A dispatch/transport failure and a started-worker failure consume an
  invocation and the failed-invocation budget. Retry the same owner and role.
- Before retrying, query the host job by its persisted id to prove the prior
  attempt is terminal; never create duplicate live workers because a wait
  timed out.
- Use bounded waits no longer than 60 seconds so Luna can report concise
  progress. Unchanged polls should not produce noisy commentary.
- Require Claude to write the structured receipt and bounded findings artifact
  before optional prose. If prose is truncated but the receipt is valid,
  continue. If the receipt is missing or truncated, make one receipt-only
  recovery request against the same completed job; this is not a new worker
  invocation. If recovery fails, record a no-verdict worker failure.
- Retry only within the configured budgets. Exhaustion enters `escalated` with
  the active state, job history, last progress, and exact failed evidence.

### Coherent correction and invocation limits

Replace the contradictory eight-invocation ceiling with limits that can
actually permit 20 correction rounds:

```toml
[limits]
max_correction_rounds = 20
max_correction_reviews_per_defect = 3
max_invocations = 48
max_failed_invocations = 6
```

The initial writer/reviewer pair consumes two successful invocations. Twenty
correction writer/reviewer pairs consume forty more, so 42 is the minimum
successful total. The 48-invocation ceiling reserves six failed attempts.
Keep `max_failed_invocations` consistent between `[limits]` and
`[continuation]`, or define it in only one block and reference it from the
other; do not maintain two independently configurable values.

Apply limits in this order after every persisted event:

1. If the same finding remains unresolved after its third correction review,
   escalate that finding.
2. If the twentieth correction round completes without milestone acceptance,
   escalate the milestone.
3. If six worker invocations fail, escalate worker/infrastructure reliability.
4. If total attempted worker invocations reaches 48 without a valid next
   action that stays within the ceiling, stop unconditionally and escalate.

Allow the stated maximum; reject the transition that would exceed it. Count
each attempted worker execution exactly once, including setup, authentication,
transport, and hard-timeout failures. Do not count polling, receipt recovery,
Terra decisions, fingerprinting, or Luna verification as worker invocations.
Update `validate_handoff.py`, the round-ledger schema, configuration validator,
documentation, and fixtures together.

### Reviewer capability preflight and fallback

Before review dispatch, record a capability preflight for the selected
reviewer: exact-snapshot access, repository inspection, shell execution, test
execution, and configured repository/CRG check execution. Exact-snapshot
inspection is mandatory; other capabilities may be supplied by Luna as trusted
external evidence.

Move trusted fingerprinting and authoritative test execution to Luna:

- Luna computes scoped and full-worktree fingerprints before and after review.
  A reviewer-supplied fingerprint is evidence only.
- Luna runs required verification commands and provides their immutable output
  and status to the reviewer in a bounded evidence bundle.
- A shell-restricted reviewer may perform static review against the exact
  frozen snapshot and Luna's evidence bundle. Its receipt must list unavailable
  capabilities and checks it did not execute.
- Missing reviewer shell access alone must not end orchestration or silently
  become approval. Luna runs the unavailable checks and Terra decides whether
  the combined independent review and trusted execution evidence satisfy the
  milestone policy.
- A reviewer that cannot inspect the exact frozen snapshot produces no
  verdict. Use the existing Terra-approved independent fallback when valid;
  otherwise enter `escalated`.
- Never let the writer review its own snapshot, including after timeout,
  capability failure, or fallback.

Extend worker receipts with a capability/preflight section and separate
`checks_performed`, `checks_supplied_by_orchestrator`, and
`checks_unavailable` fields. Validate that required evidence has exactly one
trusted source and that unavailable checks are visible to Terra.

### Luna execution loop

Update `SKILL.md` with an imperative loop Luna can follow directly:

```text
load and lock durable state
while state is non-terminal:
    reconcile persisted active job with the host
    if a job is active:
        wait for bounded progress or completion
        persist progress and continue
    if a receipt is available:
        ingest and validate it exactly once
    compute the deterministic next action
    persist the transition before its side effect
    dispatch, verify, route, retry, or escalate as selected
release lock
emit a final response only for a terminal state
```

For dispatch transitions, use a two-step intent/confirmation protocol so a
crash between persistence and host dispatch can be reconciled safely. Persist
a dispatch intent and idempotency key first; after dispatch, persist the host
job id. On resume, query by idempotency key/job id before creating another job.

### Implementation scope

At minimum, Phase 3 should update or add:

- `SKILL.md`: run-to-terminal invariant, Luna loop, terminal conditions, and
  routing summary.
- `configuration.md`, `config/example.toml`, and `validate_config.py`: closed
  continuation/limit policy and validation.
- `references/continuous-orchestration.md`: full state/event transition table,
  timeout semantics, restart reconciliation, and capability fallback.
- `scripts/orchestration_state.py`: deterministic durable state engine and
  next-action selection.
- `schemas/orchestration-event.schema.json` and
  `schemas/orchestration-state.schema.json`: closed persistence contracts.
- `worker-receipt.schema.json` and worker receipt documentation: capability
  preflight and evidence-source fields.
- `validate_handoff.py` and `round-ledger.schema.json`: coherent limits and
  evidence validation.
- `scripts/test_phase3.py`: state-machine, restart, timeout, fallback, and
  continuous-routing integration tests using deterministic fake worker/host
  adapters.
- `quick_validate.py`: include all Phase 3 schemas and tests in the skill's
  standard validation path.

Do not add a Python adapter that pretends it can invoke unavailable Codex or
Claude APIs. Keep host tool calls in Luna and make the state engine return a
closed `next_action` record that Luna executes through approved host-native
tools.

### Acceptance criteria

- A delayed fake implementation automatically transitions to review when it
  completes; no terminal response/state occurs while either job is active.
- A `needs_correction` review automatically routes to the original writer,
  freezes the corrected snapshot, and dispatches the opposite reviewer.
- An approved review automatically triggers Luna verification, Terra decision,
  and either the next milestone or task completion.
- Restarting Luna from every running and dispatching state reconstructs the
  same state, reconciles the existing host job, and never duplicates a worker
  or consumes a receipt twice.
- Tests cover progress timeout, hard timeout, transport failure, worker failure,
  receipt-only recovery, retry exhaustion, and host wait/wakeup unavailability.
- Polling and receipt recovery do not increment invocation counts; every worker
  attempt increments them exactly once and preserves role ownership.
- The 20-round, three-reviews-per-defect, six-failure, and 48-invocation limits
  accept their exact boundary and reject the first exceeding transition.
- A shell-denied reviewer can complete static review from the exact snapshot
  and Luna evidence bundle; missing checks remain explicit and require Terra's
  acceptance.
- A reviewer without exact-snapshot access produces no verdict and routes to a
  valid independent fallback or escalation.
- Scoped and full-worktree fingerprints remain unchanged across every read-only
  review, including fallback and restart paths.
- Concurrent Luna resumptions cannot both advance or dispatch the same task.
- Existing Phase 1/2 authority, Qwen isolation, failover, handoff, receipt,
  schema, and end-to-end tests remain green.
- A realistic integration test exercises implementation → review → correction
  → re-review → verification → Terra acceptance → next milestone without a
  user message or final response between automatic transitions.
