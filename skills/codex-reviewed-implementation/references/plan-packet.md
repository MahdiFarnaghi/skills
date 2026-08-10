# Compact plan-review packet

Use this template before implementation when the profile requires a plan challenge. Keep it repository-addressable and under 8,000 characters. Distill the completed packet into concise focus text for one operator-invoked scoped review command (e.g. `/codex:adversarial-review`); it is not consumed by the turn-scoped stop gate. See [codex-plugin-adapter.md](codex-plugin-adapter.md).

```md
# Codex plan review

## Objective
<one concise paragraph describing the user-visible or operational outcome>

## Authority
- Specification: <authoritative files, issue, or user contract>
- Allowed delivery actions: <commit/push/deploy boundaries>
- Human gates: <decisions that must return to the user>

## Global invariants
- <invariant that must remain true across milestones>
- <failure, compatibility, recovery, or security invariant>

## Milestones
### M1: <stable name>
- Contract: <independently correct behavior>
- Acceptance: <observable proof>
- Dependencies: <prior milestone or none>
- Non-goals: <explicit exclusion>

### M2: <stable name>
- Contract: <independently correct behavior>
- Acceptance: <observable proof>
- Dependencies: <prior milestone or none>
- Non-goals: <explicit exclusion>

## Integration strategy
- <cross-milestone verification, transition, or release gate>

## Evidence strategy
- Acceptance ledger: <location of the durable ledger or compact checklist>
- Required verification tiers: <focused/full/real-service/cross-process/deployment-shaped tiers that apply>
- Docker strategy: not applicable | <milestone-level container checks and final Docker acceptance command when the project is Dockerized> | unavailable — <exact reason and affected evidence status>

## Independent review mandate
Perform an autonomous, read-only sweep before answering any directed questions. Independently inspect this plan, the affected callers and flows, and the proposed milestones. Choose your own highest-risk attack surface and report material counterexamples, affected flows, or missing proof that the author may have missed. Do not limit the sweep to the directed questions.

## Directed questions
Answer only after the independent sweep. At most three questions; "None" is valid.
1. <optional decomposition or sequencing question>
2. <optional question>
3. <optional question>

## Known uncertainties
- <unresolved fact that does not yet require a design decision>
```

## Packet rules

- Describe contracts and observable outcomes, not speculative implementation detail.
- Keep milestone names stable after the plan is frozen.
- Identify which milestone owns every global invariant.
- Identify required real boundaries and where their evidence will be recorded.
- Declare the Docker strategy explicitly: not applicable, the containerized checks and final command, or unavailable with the exact reason.
- Make dependencies directional and explicit.
- Include fresh-install, transition, rollback, recovery, or deployment concerns only when relevant.
- Ask zero to three directed questions; never invent questions to fill the section.
- Keep the packet as routing information, not a reviewer-authored threat model.
- Never include secrets, credentials, private data, conversation transcripts, or long source excerpts.
- Do not create a repository artifact solely to manufacture a Git review target.
