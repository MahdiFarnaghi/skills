# Design decisions

This file holds historical context that should not be loaded as current
workflow instructions.

## Adapter and review phases

The skill moved from an operator-only plugin path to a public Codex CLI adapter.
The current transport contract and fallback behavior live in
`codex-cli-adapter.md`; old workstream numbering and delivery milestones are
not part of the runtime contract.

## Execution-profile boundary

The wrapper binds model, reasoning effort, wrapper profile name, config version,
and effective native model/effort pins. It deliberately does not parse the
contents of a native Codex `-p` profile. A change to that external profile
requires a fresh live doctor receipt.
