---
name: codex-orchestrated-implementation
description: Coordinate substantial or high-risk implementation work with a confirmed Codex orchestrator and a reciprocal two-member Codex/Claude Code pair. Use when work benefits from bounded milestones, isolated implementation, independent read-only review, correction loops, and orchestrator-controlled verification; do not use for routine small fixes unless explicitly requested.
---

# Codex-Orchestrated Implementation

Use Terra (or another explicitly confirmed Codex orchestrator) as the sole
workflow and completion authority. Use exactly two equivalent pair members.
Roles are derived per milestone; never configure one member permanently as
implementer or reviewer.

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
