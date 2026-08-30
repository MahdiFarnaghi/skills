# Plan

## Phase 1 — Add Qwen3.8-27B as a constrained local worker

Integrate the server-hosted Qwen3.8-27B model as an optional `qwen_local`
worker in `codex-orchestrated-implementation`. Terra remains the orchestrator
and sole completion authority; Qwen is not an implementation/review-pair
replacement for Luna or Claude.

### Scope

- Add a strict `qwen_local` provider configuration and a local-server adapter
  with capability, authentication, and model-identity checks.
- Allow Qwen only for bounded, lower-risk tasks: repository mapping, focused
  read-only investigation, prescribed test execution and failure summaries,
  narrowly scoped mechanical edits, and draft documentation or test artifacts.
- Require a compact task packet, explicit file/path scope, fixed token budget,
  low or medium reasoning effort, and a structured worker receipt.
- Route every Qwen result through Terra’s receipt, fingerprint, and evidence
  validation before it can influence a milestone.

### Exclusions

- No architecture ownership, task decomposition, final acceptance, or worker
  conflict resolution.
- No security, privacy, durable-state, migration, concurrency, recovery,
  compatibility, deployment, destructive, or external-action decisions.
- No independent final review and no autonomous skill/tool selection.
- No long, unbounded, or transcript-heavy sessions; split work into compact
  task packets when the relevant context would exceed the configured budget.

### Acceptance criteria

- Configuration rejects an unavailable local server, unknown Qwen model, or
  unsupported worker role with a stable error.
- The adapter records endpoint identity, model, context/token limits, effort,
  and capability result in every receipt.
- Integration tests prove permitted tasks can return a valid receipt and that
  prohibited roles/actions are rejected before dispatch.
- Terra continues to enforce frozen handoffs, read-only review, accounting,
  and final acceptance regardless of Qwen availability or failure.
