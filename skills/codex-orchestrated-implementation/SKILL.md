---
name: codex-orchestrated-implementation
description: Coordinate substantial or high-risk implementation work with a confirmed Codex orchestrator and a reciprocal two-member Codex/Claude Code pair. Use when work benefits from bounded milestones, isolated implementation, independent read-only review, correction loops, and orchestrator-controlled verification; do not use for routine small fixes unless explicitly requested.
---

# Codex-Orchestrated Implementation

Use Terra as the sole technical authority and final acceptance authority. Luna
is the execution orchestrator. The reciprocal pair is exactly
`luna_worker`/`claude_worker`; roles are derived per milestone and alternate,
so neither member is permanently an implementer or reviewer. Version 2 is the
Phase 1/2 contract. Version 1 remains a legacy path with its original
validation and semantics; it is never silently reinterpreted as v2.

## Phase 1: control plane and immutable evidence

V2 separates `[technical_authority]` (Terra) from `[orchestrator]` (Luna) and
fixes the pair names and provider/model roles. Every v2 worker receipt and
ledger binds the four runtime roles with distinct `role_instance_id`,
`session_id`, `host_id`, and `context_id` values plus resolved execution
profiles. Reusing any identity across Terra, the Luna orchestrator, the Luna
worker, or the Claude worker is rejected. The technical-authority profile is
evidence of the resolved runtime, not a prompt or TOML promise.

Review remains read-only. In addition to the declared-scope fingerprint, v2
reviews carry a full-worktree before/after fingerprint. Terra rejects any
reviewer mutation outside the declared scope as well as inside it.

The decision protocol in `scripts/decision_protocol.py` stores durable,
append-only request and decision JSONL records. Records bind task, milestone,
profiles, absolute worktree, exact scope, target fingerprint, and policy
digest. Idempotency is canonical-content based; supersession appends a new
request and cannot rewrite or decide an older request; an unavailable Terra
may emit only an unavailable status, never a decision.

## Phase 2: constrained Qwen assistant

`qwen_local` is optional and disabled/read-only by default. The
`QwenLocalAdapter` speaks only the allowlisted OpenAI-compatible local endpoint
and `Qwen3.8-27B` model after a capability probe. TLS/auth—including an
optional bearer token from an explicitly named environment variable such as
`LLM_BEARER_TOKEN`—low/medium
reasoning, timeout/retry, context/input/output budgets, and disabled
tools/skills/inherited credentials are strict policy. Qwen output is always
untrusted and has its own structured receipt; disabled, out-of-policy, and
unprobed dispatches fail with stable rejection codes.

## Worker failover

Claude quota exhaustion is a failed invocation, not a review verdict. Luna may
continue bounded approved work, but may not self-review the same snapshot. A
Claude-to-Luna fallback requires a distinct Luna worker instance, session, and
context plus an append-only Terra decision and validated `scripts/failover.py`
record. Claude implementation ownership cannot transfer silently. If no
independent reviewer is available, pause and escalate.

Qwen cannot write the active worktree. The only mechanical-write path is an
explicitly enabled isolated `draft_patch` gate with a Terra `accept` decision,
passing integration tests, adoption by one pair worker, and read-only review
by the opposite pair worker. Missing any gate is a rejection.

## Start safely

1. Require an accessible Git repository. This skill has no non-Git mode:
   handoffs bind a full Git HEAD, Git worktree, and Git-derived fingerprints.
   Stop and escalate if the target is not a Git worktree.
2. Read repository instructions and inspect existing ownership, branches,
   worktrees, staged/unstaged/untracked state, and applicable verification
   commands. Preserve unrelated changes.
3. Require `<project-root>/.codex-orchestration.toml`. If it is missing, ask
   the developer to create it from `<skill-root>/config/example.toml`, select
   the intended pair, and then run:

   ```sh
   python3 <skill-root>/scripts/validate_config.py --config <project-root>/.codex-orchestration.toml
   ```

   A missing or invalid configuration is a hard stop. Use
   [configuration.md](configuration.md) for the closed schema.
4. Resolve the actual current orchestrator execution profile before any worker
   writes: provider, model, reasoning effort, host/profile identity,
   resolution source, and profile digest. Record it in the task ledger and
   every worker receipt. A TOML value or prompt cannot select the current
   orchestrator. If the profile cannot be confirmed, stop and escalate.
5. Select Lightweight, Standard, or Safety-critical. Use the least profile
   that is safe; escalate when risk appears. Load [safety-lifecycle.md](references/safety-lifecycle.md)
   for Standard/Safety-critical routing and verification requirements.
6. Establish objective, acceptance criteria, invariants, non-goals, exact path
   scope, verification commands, and authorized delivery actions. For Standard
   and Safety-critical work, maintain the plan packet and acceptance ledger.

## Reciprocal pair workflow

The configured `first_implementer` writes milestone 1 and the other pair
member reviews it; they swap for every substantive milestone. For each
milestone, assign exactly one writer and one independent read-only reviewer.
The writer may edit only the frozen path scope. The reviewer inspects the
repository, callers, tests, and evidence and must not modify, stage, commit,
delete, publish, or repair production files.

Use the available Codex delegation mechanism for Codex workers. For Claude
Code implementation/editing, use the installed `$cc:rescue --write` capability
with the exact worktree and scope. Use `$cc:review` for Claude Code review only
when its execution environment is already bound to the exact frozen worktree;
it accepts neither an arbitrary path scope nor a worktree argument and it does
not emit this skill's receipt. Terra must independently create and validate the
receipt. Otherwise use a Codex read-only reviewer or escalate. Do not launch
an unmanaged Claude process or claim that a prompt changed model selection.

At every implementation-to-review boundary follow
[handoff-protocol.md](references/handoff-protocol.md). It requires an exact
absolute worktree, full baseline identity, explicit path scope, resolved
orchestrator profile, and target fingerprints before and after review. The
reviewer must review the same frozen snapshot: its before fingerprint equals
the writer's handoff fingerprint and its before/after fingerprints are equal.
Validate the records with `scripts/validate_handoff.py`; it enforces the
semantic pair, snapshot, profile, policy, and limit rules and can idempotently
append receipts and ledger entries to JSONL stores. Generate every boundary
fingerprint with its `--fingerprint-worktree` mode; validation recomputes the
current scoped Git worktree fingerprint rather than trusting receipt values.

Require each worker to return the structured [worker receipt](references/worker-receipt.md).
Terra validates receipts against the actual worktree and commands; a receipt
is evidence, not a verdict. Classify findings as `accept`, `disprove`,
`defer`, or `escalate`. Only the milestone's original implementer may apply
accepted corrections. Freeze the corrected snapshot and have the opposite
member re-review it.

## Accounting and completion

An invocation is every attempted worker execution, including setup,
transport, authentication, and timeout failures. An initial round is one
implementation invocation plus one review invocation over one frozen snapshot.
A correction round is one invocation by the original implementer plus one
re-review over the new snapshot. A failed invocation produces no verdict and
does not complete a round; retrying consumes another invocation and does not
change ownership. Stop and escalate at two correction reviews for one defect,
four correction rounds, or eight total invocations per milestone. See the
handoff reference for Lightweight exemptions; none remove single-writer,
read-only-review, profile-confirmation, receipt, fingerprint, or accounting
requirements.

Run focused and applicable full, real-boundary, cross-process, deployment, and
operator checks after each milestone and at final integration. Do not treat
passing tests as proof that a finding is disproved. Safety-critical work must
model failure, concurrency, recovery, rollback, and cleanup before acceptance.
Terra alone declares milestone/task completion and reports selected profiles,
role assignments, evidence, failures, skips, limitations, and authorized
delivery actions. Use [plan-packet.md](references/plan-packet.md) for the
bounded plan and [worker-receipt.md](references/worker-receipt.md) for receipt
fields.
