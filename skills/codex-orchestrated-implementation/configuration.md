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

The closed schema is:

```toml
version = 1

[orchestrator]
provider = "codex"
model = "gpt-5.6-terra"
reasoning_effort = "high"

[[pair]]
name = "worker_a"
provider = "claude_code"
model = "opus"
enabled = true

[[pair]]
name = "worker_b"
provider = "codex"
model = "gpt-5.6-luna"
enabled = true

[workflow]
profile = "standard"
pairing_mode = "alternating"
first_implementer = "worker_a"
```

There must be exactly two enabled `[[pair]]` tables. Members are equivalent
pair members; neither has a configured role. With `alternating`, the named
`first_implementer` writes milestone 1, and the other member reviews it; the
writer/reviewer roles swap for every substantive milestone. The only supported
pairing mode is `alternating`. A `codex` model must be a syntactically valid
lowercase `gpt-*` identifier; current host compatibility is confirmed at
runtime with the resolved execution profile, not by a stale local catalog.
`claude_code` accepts the companion's `fable`, `opus`, `sonnet`, and `haiku`
aliases. Claude Code may still reject an alias unavailable to the signed-in
account.

The `[orchestrator]` block is a requested policy, not proof that the current
orchestrator is Terra. Before the first write, resolve the actual host
execution profile (provider, model, reasoning effort, host/profile identity,
and resolution source). Record that immutable profile in every ledger and
receipt. If the host cannot confirm it, stop and escalate; a prompt or TOML
value cannot switch the current orchestrator.
