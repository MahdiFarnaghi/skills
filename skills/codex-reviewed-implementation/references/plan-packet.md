# Compact plan-review packet

Use this template before implementation when the profile requires a plan challenge. Keep it repository-addressable and under 4,000 characters. Validate it with `scripts/validate_review_packet.py`, then pass it to the wrapper's `review` subcommand. The packet routes attention; it is not proof.

```md
# Codex plan review

## Objective
<one concise paragraph>

## Authority
- Specification: <authoritative files or user contract>
- Allowed delivery actions: <boundaries>
- Human gates: <decisions returned to the user>

## Global invariants
- <invariant>

## Milestones
### M1: <stable name>
- Contract: <independently correct behavior>
- Acceptance: <observable proof>
- Dependencies: <prior milestone or none>
- Non-goals: <explicit exclusion>

## Integration strategy
- <cross-milestone verification or release gate>

## Evidence strategy
- Acceptance ledger: <location or compact checklist>
- Required verification tiers: <applicable tiers>
- Docker strategy: not applicable | <commands and services> | unavailable — <reason>

## Directed questions
None.

## Known uncertainties
- <unresolved fact>
```

## Packet rules

- Describe contracts and observable outcomes, not speculative implementation detail.
- Identify global-invariant ownership, dependencies, real boundaries, Docker applicability, and rollback/recovery concerns when relevant.
- Ask zero to three directed questions; never invent questions to fill the section.
- Never include secrets, private data, conversation transcripts, or long source excerpts.
