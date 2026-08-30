# Worker receipt

Every worker invocation must provide a compact receipt conforming to
`schemas/worker-receipt.schema.json` with:

- milestone and task id;
- invocation id, milestone index, round, provider, model, derived runtime
  role, policy digest, and the resolved orchestrator execution profile;
- absolute worktree, full baseline identity, exact path scope, and target
  fingerprints before and after the invocation;
- whether the worker was read-only or changed files;
- files changed or inspected;
- commands and verification results;
- failures, warnings, and unresolved limitations;
- implementation or review conclusion;
- next recommended action.

Generate each target fingerprint with
`scripts/validate_handoff.py --fingerprint-worktree <absolute-worktree>
--scope-path <path> ...`. Terra validates the receipt against the actual
worktree and commands with `scripts/validate_handoff.py`. For a review,
`target_fingerprint_before` must equal the writer's handoff fingerprint and
`target_fingerprint_after` must equal its before value. A missing, stale,
contradictory, duplicate, or scope-mismatched receipt is not a verdict. The
helper's optional JSONL store is append-only; a repeated invocation id is
accepted only when its canonical content has the same digest, making
restart/re-entry idempotent.
