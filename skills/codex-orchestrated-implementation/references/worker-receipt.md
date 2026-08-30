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

V2 receipts additionally include the worker's `role_instance_id`,
`session_id`, `host_id`, `context_id`, and resolved worker profile. The v2
ledger carries matching runtime bindings for exactly `technical_authority`,
`orchestrator`, `luna_worker`, and `claude_worker`; all four values for each
identity field must be distinct. V2 receipts also include full-worktree
fingerprints, and a reviewer must prove that its full before/after values are
unchanged.

The optional Qwen assistant never uses a worker receipt. It emits the
structured `schemas/qwen-receipt.schema.json` receipt, marked read-only and
untrusted. Its output is evidence only and cannot become a verdict or direct
worktree change.
