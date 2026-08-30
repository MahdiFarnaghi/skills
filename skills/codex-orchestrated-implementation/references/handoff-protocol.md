# Immutable pair handoff protocol

Use this protocol for the implementation-to-review transition and for every
correction re-entry. It applies to Lightweight as well as Standard and
Safety-critical work.

1. Terra resolves and records one execution profile before the first write:
   provider, model, reasoning effort, host/profile identity, resolution source,
   and profile digest. The resolved profile is runtime evidence, not a config
   promise. Missing or contradictory confirmation is an escalation. Under v2,
   the ledger also binds the separate Luna orchestrator profile and the two
   named pair-worker profiles. Each of those four runtime bindings has
   distinct role-instance, session, host, and context identities.
2. Require an accessible Git worktree. This protocol has no non-Git mode:
   the baseline is a full `HEAD` object id and the helper verifies it against
   the named worktree.
3. The implementing member records the absolute worktree path, milestone id,
   baseline identity (full HEAD commit plus baseline dirty-state fingerprint),
   and an explicit path scope. The scope must include every path the milestone
   may change. Do not use a branch name or a path prefix as a substitute for
   these identities. `baseline.dirty_state_fingerprint` is a historical
   point-in-time value: generate it with the command below before the write;
   the helper checks it is immutable across records but does not claim it can
   be revalidated after implementation changes the scope.
4. Before handing off, compute `target_fingerprint_after` over the scoped
   worktree state, including staged, unstaged, and untracked content. Freeze
   the worktree. The handoff receipt must include the policy digest and the
   resolved orchestrator execution profile.
5. The reviewer must independently confirm the same absolute worktree, full
   baseline identity, exact path scope, and exact implementation fingerprint
   before reading. A mismatch is no verdict: stop and re-establish the handoff.
6. Review is read-only. The reviewer records `target_fingerprint_before` and
   `target_fingerprint_after`; they must be equal. Under v2 it also records
   `full_worktree_fingerprint_before` and `full_worktree_fingerprint_after`;
   those must be equal and are recomputed over `.` so an out-of-scope reviewer
   mutation cannot bypass the guard. A reviewer must not modify,
   stage, commit, delete, publish, or repair production files. Use `$cc:review`
   only when its environment is already bound to this exact frozen worktree;
   it cannot accept the path scope/worktree or emit the required receipt.
   Terra must independently create and validate that receipt. Otherwise use a
   Codex read-only reviewer or escalate. Use `$cc:rescue --write` only when
   Claude Code is the implementation worker.
7. Terra classifies findings. Only the member that implemented this milestone
   may correct them. After correction, issue a new receipt and new target
   fingerprint; the other member re-reviews that new, frozen snapshot.

The receipt and ledger schemas in `schemas/` are structural contracts. Run the
semantic check before accepting a review:

```sh
python3 <skill-root>/scripts/validate_handoff.py --fingerprint-worktree <absolute-worktree> --scope-path <path> --scope-path <path>
python3 <skill-root>/scripts/validate_handoff.py --config <project-root>/.codex-orchestration.toml --implementation-receipt <implementation.json> --review-receipt <review.json> --ledger <ledger.json> --receipt-store <receipts.jsonl> --append-receipts --ledger-store <ledger.jsonl> --append-ledger
```

Run the first command at baseline, after implementation, and immediately
before/after review; record the returned digest in the corresponding receipt
field. The deterministic digest covers scoped Git index entries, staged and
unstaged status (including deletion), untracked status, and a byte-level
filesystem manifest. Paths outside the declared scope are deliberately absent.
The semantic check recomputes the current scope and requires it to equal the
implementation handoff and both review fingerprints.

The helper also checks policy/profile consistency, pair roles, immutable
worktree / baseline / scope, Git HEAD, and the round limits. Receipt storage
admits only unseen invocation ids; ledger storage admits only one canonical
`task_id`/`milestone_id`/`round` entry. Identical re-entry is a no-op and a
conflict fails. Never overwrite an accepted round or replay an accepted
finding.

This helper validates only a completed pair handoff: the implementation
outcome must be `success`; the review outcome must be `approve` with ledger
status `reviewed`, or `needs_correction` with ledger status
`needs_correction`. Transport, setup, timeout, and failed-worker outcomes are
no-verdict states: record the attempted invocation for accounting, but do not
pass it to the completed-pair handoff validator or append it as a completed
pair ledger entry.

## Accounting

An **invocation** is one attempted worker execution, including a transport,
authentication, setup, or timeout failure. An initial **round** is one
implementation invocation followed by one read-only review invocation over the
same frozen snapshot. A **correction round** is one invocation by the original
implementer followed by one re-review by the opposite member over the new
snapshot. A failed invocation does not produce a verdict and does not complete
a round; retrying it consumes another invocation and keeps the same owner and
role.

Per milestone, stop and escalate at the first applicable limit: three correction
reviews for one defect, 20 correction rounds, or eight total invocations
(including failed attempts and the initial pair). A worker/transport failure
never silently changes ownership. Re-plan with Terra before any reassignment;
if the original implementer is unavailable, the milestone remains incomplete.

Lightweight may omit the durable acceptance ledger, plan packet, and separate
final integration review for its one bounded milestone. It may not omit strict
config validation, execution-profile confirmation, exact handoff identities,
single-writer ownership, read-only review, receipts, focused verification, or
the limits above.
