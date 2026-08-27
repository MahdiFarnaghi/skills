# Compact review packet

Use this template at a milestone review boundary. Keep it factual, repository-addressable, and under 4,000 characters. Omit empty optional sections. Validate it with `scripts/validate_review_packet.py`, then pass it to the wrapper's `review` subcommand. The packet routes attention; it is not proof.

```md
# Milestone review

Milestone: <id>
Baseline: <ref>
Scope:
- path:symbol

Contract:
- <observable outcome>
- <failure invariant>

Non-goals:
- <scope exclusion>

Verification:
- `<command>` — <result>

Evidence gaps:
- none | <gap>

Prior findings:
- <correction rounds only>

Directed questions:
- none | <up to two questions>
```

## Packet rules

- List paths and symbols; do not paste diffs or long logs.
- State only results observed in the current worktree and name a resolvable baseline.
- Keep the contract independent of implementation history and ask zero to three counterexample-oriented questions.
- Findings in the structured verdict must identify whether they came from the independent sweep or directed challenge using the schema-bound `origin` field.
- Never include secrets, credentials, private data, or conversation transcripts.
