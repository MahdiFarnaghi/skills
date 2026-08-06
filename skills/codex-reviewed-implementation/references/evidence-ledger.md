# Acceptance-evidence ledger

Maintain one compact source of truth for acceptance and verification. Use the repository's existing implementation report when available; otherwise keep the ledger in authorized task-local state.

```md
| AC | Invariant or behavior | Implementation | Unit and failure evidence | Real-boundary evidence | Docs | Status or limitation |
|----|-----------------------|----------------|---------------------------|------------------------|------|----------------------|
```

Use only these status meanings:

- `planned`: scoped but implementation has not started.
- `implemented`: code exists but required verification is incomplete.
- `focused_verified`: focused success, failure, and regression tests passed.
- `integration_verified`: required real-service or cross-component checks passed.
- `system_verified`: required cross-process, containerized, deployment-shaped, or full-flow checks passed.
- `partially_verified`: named required evidence is missing or inconclusive.
- `skipped`: a required check was not executed; record the exact reason.
- `blocked`: completion needs user authority or an external-state change.
- `complete`: implementation, required evidence, documentation, review, and cleanup are complete.

Do not advance a status from inferred, earlier-session, indirectly covered, flaky, or structurally similar evidence.

Also record compact tables when relevant:

```md
## Issues and resolutions
| Symptom | Root cause | Resolution | Regression evidence | Status |

## Architectural decisions
| Decision | Invariant protected | Alternatives rejected | Evidence |

## Verification runs
| Command or scenario | Environment and real boundaries | Passed | Failed | Skipped | Warnings, repetitions, or flakes |
```

For every run, record the exact command or scenario, relevant environment, real versus mocked boundaries, counts, skips, warnings, repetitions, and flaky outcomes. Replace superseded exploratory detail with the durable conclusion; do not turn the ledger into a chronological transcript.
