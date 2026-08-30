# Configuration contract

An explicit project policy is required before orchestration. If it is missing,
create `<project-root>/.codex-orchestration.toml` from
`<skill-root>/config/example.toml`, set the intended pair and profile, then
validate it:

```sh
python3 <skill-root>/scripts/validate_config.py --config <project-root>/.codex-orchestration.toml
```

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
model = "opus"
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
are disabled. `write_mode = "draft_patch"` only permits a separately gated,
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
