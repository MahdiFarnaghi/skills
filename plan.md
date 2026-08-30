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
