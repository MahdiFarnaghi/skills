# Compact review packet

Use this template at a milestone review boundary. Keep it factual, repository-addressable, and under 8,000 characters. Omit empty optional sections. Validate it with `scripts/validate_review_packet.py`, then pass it as the `--packet` argument to `scripts/run_codex_review.py review` (see [codex-cli-adapter.md](codex-cli-adapter.md)): it names the milestone, the Git scope, and the focus for the reviewer, which inspects the working-tree diff independently. The packet routes attention; it is not proof, and it is not consumed by the turn-scoped stop gate.

```md
# Codex milestone review

## Identity
- Milestone: <short stable name>
- Baseline: <commit or initial worktree baseline>
- Scope: <exact files and important symbols>
- Diff mode: <isolated since baseline | cumulative working tree>

## Contract
- <observable behavior or invariant>
- <observable failure/replay/compatibility behavior>

## Non-goals
- <explicit deferred behavior>

## Decisions
- <material choice and concise rationale>

## Verification
- `<exact command>`: <exact result>
- `<exact command>`: <exact result>

## Evidence status
- Acceptance criteria: <complete | focused_verified | integration_verified | system_verified | partially_verified | blocked>
- Real boundaries: <database/process/container/API/etc. actually exercised>
- Docker: not applicable | <exact containerized command and services exercised> | unavailable — <exact reason and affected evidence status>
- Missing evidence: <none or exact gap>

## Independent review mandate
Perform an autonomous, read-only sweep before answering any directed questions. Independently inspect the specification, this diff, affected callers and flows, the tests, and the evidence. Choose your own highest-risk attack surface and report material counterexamples, affected flows, or missing proof that the author may have missed. Do not limit the sweep to the directed questions.

## Directed questions
Answer only after the independent sweep. At most three questions; "None" is valid.
1. <highest-risk counterexample question>
2. <second question, if needed>
3. <third question, if needed>

## Prior findings
- <accepted/disproved/deferred finding and evidence; correction reviews only>

## Known limitations
- <truthful limitation that does not invalidate this milestone>
```

## Packet rules

- List paths and symbols; do not paste diffs or long logs.
- State only results actually observed in the current worktree.
- Treat passing tests as evidence, not proof of the contract.
- State the evidence status, which critical boundaries were real, and the Docker applicability and evidence.
- Name a baseline that Codex can resolve locally.
- Mark a cumulative working tree honestly when no accepted checkpoint commit exists.
- Keep the contract independent of implementation history.
- Ask zero to three directed questions, each capable of producing a concrete counterexample; "None" is valid.
- Never include secrets, credentials, raw sensitive payloads, or private user data.
- Do not ask for broad quality review when a precise failure mode can be named.
