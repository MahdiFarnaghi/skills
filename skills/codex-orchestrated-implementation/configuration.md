# Configuration contract

An explicit project policy is required before orchestration. If it is missing,
create `<project-root>/.codex-orchestration.toml` from
`<skill-root>/config/example.toml`, set the intended pair and profile, then
validate it:

```sh
python3 <skill-root>/scripts/validate_config.py --config <project-root>/.codex-orchestration.toml
```

The project-local policy should be listed in the target repository’s
`.gitignore` (the exact entry is `.codex-orchestration.toml`). The example
template is safe to distribute, but the resolved policy is environment-specific
and may reference local endpoints or authentication settings.

The validator is the enforcement point. It parses TOML with Python's strict
`tomllib` parser and rejects malformed TOML, unknown keys, missing fields,
duplicate member names, disabled or extra members, unsupported pairing modes,
invalid `first_implementer`, role declarations, and provider/model mismatches.
It prints a canonical policy digest for receipts. A failed validation is a
hard stop; it does not fall back to defaults.

Version 1 remains accepted with its original semantics. A v1 policy has one
requested orchestrator and two equivalent, role-less pair members; roles are
derived from `first_implementer`. It is not silently upgraded to v2.

The v2 closed schema is:

```toml
version = 2

[technical_authority]
provider = "codex"
model = "gpt-5.6-terra"
reasoning_effort = "high"

[orchestrator]
provider = "codex"
model = "gpt-5.6-luna"
reasoning_effort = "high"

[[pair]]
name = "luna_worker"
provider = "codex"
model = "gpt-5.6-luna"
enabled = true

[[pair]]
name = "claude_worker"
provider = "claude_code"
model = "glm-5.3[1m]"
enabled = true

[workflow]
profile = "standard"
pairing_mode = "alternating"
first_implementer = "luna_worker"
```

V2 requires exactly `luna_worker` and `claude_worker`, both enabled. Terra is
the only technical authority and final acceptance authority. Luna is the
execution orchestrator. The two named workers alternate implementation and
read-only review by substantive milestone; neither may be configured as a
permanent role. V2 enforces `-terra` and `-luna` model suffixes for the two
control-plane roles. The current host still confirms runtime compatibility.

`[qwen_local]` is optional; when omitted it resolves to disabled. If present,
all fields are required and validated. Its endpoint and model must be in their
allowlists, TLS/auth policy must be explicit, budgets must fit, reasoning is
limited to low/medium, and tools, skills, inherited credentials, and writes
are disabled. When `auth_mode = "explicit_env"`, `auth_token_env` names an
uppercase environment variable containing the bearer token; the conventional
value is `LLM_BEARER_TOKEN`. The secret is never placed in TOML, receipts, or
logs. `write_mode = "draft_patch"` only permits a separately gated,
isolated draft path; it never grants direct worktree authority.

For v1, there must be exactly two enabled `[[pair]]` tables. Members remain
equivalent role-less pair members and all original provider/model and
alternation semantics remain unchanged.

The `[orchestrator]` block is a requested policy, not proof that the current
orchestrator is Terra. Before the first write, resolve the actual host
execution profile (provider, model, reasoning effort, host/profile identity,
and resolution source). Record that immutable profile in every ledger and
receipt. If the host cannot confirm it, stop and escalate; a prompt or TOML
value cannot switch the current orchestrator.

V2 failover is disabled unless `[failover]` explicitly enables it. Even when
enabled, `require_terra_approval` must remain true and fallback is limited to a
distinct Luna worker execution. Quota exhaustion produces no verdict; the
transition must be recorded and bound to the frozen scope and Terra decision.

V2 also requires `[continuation]` and `[limits]`. Continuation controls are
bounded: polling is at most 60 seconds, progress timeout is at least the poll
interval, and implementation/review/retry budgets are finite. The shipped
policy uses 60-second polling, 600-second progress timeout, 3600-second
implementation timeout, 2400-second review timeout, four transport retries,
and two worker retries. Limits are 20 correction rounds, three reviews per
defect, 48 total worker invocations, and six failed invocations. The total
invocation limit must cover the initial pair, all configured correction rounds,
and the failed-invocation budget.

The continuation policy also controls the host supervisor boundary. The lease
duration must be at least the polling interval; reconciliation attempts are
finite; foreground waiting is required by default; and the finalization gate
is mandatory. These fields are closed and unknown keys are rejected:

```toml
lease_duration_seconds = 120
max_reconciliation_attempts = 3
require_foreground_wait = true
require_finalization_gate = true
```

Polling and wakeups never count as worker invocations. A lease handoff first
reconciles the persisted job id, and a final response is allowed only after
the terminal state, host side effects, receipts, Terra decision, wakeup, and
lease are all resolved.
