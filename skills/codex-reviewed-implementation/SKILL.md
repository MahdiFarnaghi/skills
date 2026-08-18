---
name: codex-reviewed-implementation
description: Plan, implement, verify, and deliver substantial or high-risk software changes in bounded milestones, with Claude Code as the sole writer and Codex as an independent, read-only reviewer under autonomous CLI supervision at each milestone boundary. Trigger implicitly only for dual-model work involving durable-state, concurrency, migration, security, privacy, financial, compatibility, or cross-stack risk — not routine fixes, small refactors, or documentation-only changes. When invoked explicitly by name, use it regardless of task size.
---

# Codex-Reviewed Implementation

Own implementation continuously; Claude is the sole production-artifact writer. Treat Codex as an independent, read-only reviewer under autonomous supervision: at each boundary, invoke it directly through the Codex CLI wrapper (`scripts/run_codex_review.py`), evaluate its structured findings, fix accepted problems, rerun verification, and re-review until it returns a valid scoped `verdict: approve`, then stop. Autonomous supervision requires both the free preflight and a matching successful live-doctor receipt for the installed CLI/transport/schema tuple — see [references/codex-cli-adapter.md](references/codex-cli-adapter.md).

## Select a workflow profile

Before planning, select and record one profile. Use the least ceremonial profile that is actually safe; escalate to a stronger profile as soon as new risk appears. Never downgrade merely to avoid tests or review. Honor an explicit user override unless it would violate repository or safety requirements. Docker-first verification still applies in any profile when the relevant acceptance boundary is containerized.

### Lightweight

Eligible only for explicitly invoked, single-milestone, low-risk changes with no security, privacy, financial, durable-state, concurrency, migration, compatibility, recovery, cross-stack, or deployment risk. Use one compact contract and checklist rather than a durable evidence artifact; skip the Codex plan challenge; run one red-green-refactor loop; run focused and repository-required verification; run one independent Codex milestone review under autonomous supervision (or, if preflight/doctor fails and the user approves, one operator-mediated review); treat that milestone review as the final integration review unless its findings expose broader risk. Stop at the milestone boundary once Codex approves.

### Standard

Default for substantial multi-step work without safety-critical state semantics. Use milestone planning, compact acceptance evidence, the applicable verification tiers, milestone reviews, documentation reconciliation, and a final integration review. Make the Codex plan challenge conditional on dependency or sequencing risk.

### Safety-critical

Required for security, privacy, financial, durable-state, distributed, concurrency, recovery, schema-transition, compatibility, or high-impact operational work. Require the full safety lifecycle ([references/safety-lifecycle.md](references/safety-lifecycle.md)), a durable acceptance ledger, the Codex plan challenge, a failure and concurrency model, applicable real-service, cross-process, and Docker verification, milestone reviews, and a final integration review.

## Model policy (Phase 7)

Every review runs under an explicit **execution profile** — the model, reasoning effort, and policy provenance — that is resolved once, recorded in every doctor receipt, review receipt, ledger round, and quota observation, and bound by digest: a receipt for one profile never authorizes another.

**Resolution precedence** (full detail in [references/codex-model-selection.md](references/codex-model-selection.md)):

1. CLI flags: `--model <id|cli-default>`, `--reasoning-effort`, `--codex-profile`.
2. The worktree's `.codex-review.toml` (`version = 1`, `model`, `reasoning_effort`, `codex_profile`; malformed config is a loud error, never a silent default).
3. With no policy anywhere, the wrapper records the native default (`resolution_source: user_config` or `cli_default`) and warns — zero-config projects keep working exactly as before.
4. `--require-config` (CI) turns case 3 into a fast `E_CONFIG_MISSING` failure instead.

**Agent decision tree (no config present, non-interactive):** resolve the recorded native default and proceed with the warning, or pin a policy yourself with `init-config --worktree <path> --model <model> [--reasoning-effort <e>]` (idempotent; never blocks on a prompt). A human at a terminal can run `init-config` bare for the interactive picker. `validate-config --worktree <path>` prints the fully resolved profile and which level won; `list-models --json` lists available models (advisory: compatibility from the CLI's own cache, API prices labeled as not reflecting subscription quota).

Profile changes have consequences: a new profile needs a fresh doctor receipt, and re-entering a completed ledger round under a different profile returns `profile_changed` — advance to the next round number; the old verdict stays bound to its profile.

## Preconditions

1. Read the repository instructions, authoritative task artifacts, current-system documentation, and relevant design decisions.
2. Run the free transport preflight: `python3 scripts/run_codex_review.py preflight --schema schemas/codex-review-output.schema.json --worktree <abs-root>`. It verifies CLI/auth/schema/parser prerequisites without spending usage.
3. Require a successful doctor receipt matching the exact CLI version, platform, **execution profile** (model, reasoning effort, config version, profile digest), transport version, and schema digest. If none exists (or any of those changed), run the explicit paid check once: `python3 scripts/run_codex_review.py doctor --worktree <abs-root> --schema schemas/codex-review-output.schema.json --receipt <outside-worktree-doctor.json> [--model <model>] [--reasoning-effort <e>]`. Keep the profile unchanged for every review using the receipt — or re-run doctor and advance to a new review round. The doctor proves plain `codex exec` returns output conforming to the production verdict schema and preserves read-only state. If preflight or doctor fails, STOP before implementation and offer operator-mediated review only as an explicit, user-approved fallback. Never silently downgrade.

Before planning, inspect registered worktrees, active branches, HEAD, tracked and untracked changes, and any existing task ownership. Do not begin when another branch, worktree, agent, or process owns overlapping scope until ownership is reconciled. Use an isolated branch or worktree when repository policy requires it or when separation materially reduces risk; never create one without respecting user authorization and existing work.

Detect whether the project is Dockerized by inspecting repository instructions, Compose files, Dockerfiles, container-oriented test commands, and deployment documentation. Record the authoritative container commands, required services, schema/bootstrap steps, health checks, volumes, and teardown procedure before planning verification.

## Review supervision mode

Supervision is autonomous: Claude invokes Codex directly through `scripts/run_codex_review.py`, bound to the exact implementation worktree, read-only, with closed stdin and a bounded timeout. The wrapper returns a structured verdict validated locally against `schemas/codex-review-output.schema.json`; Claude classifies findings, fixes accepted ones, reruns verification, and re-invokes until a valid scoped `verdict: approve`, then stops at the boundary. The user intervenes only at approval boundaries or genuine escalations.

This mode uses plain structured `codex exec -C <worktree> -s read-only --output-schema <schema> -o <out> --ephemeral --ignore-rules <generated-review-prompt>`. Do not use `codex exec review`: its native selectors conflict with the required packet prompt and its review operation does not reliably propagate structured-output enforcement. The wrapper supplies the reviewer contract, independently resolves immutable Git identities, embeds exact scope commands, fingerprints the target, and validates the echoed identity. Never call private companion scripts, use write-capable delegation as review, or allow Codex to write production artifacts.

Recursion and boundary protection are layered: the wrapper's prompt forbids loading Claude-facing skill, companion, or orchestration instructions, and `--ignore-rules` prevents project `.rules` from overriding that boundary. Codex performs the review directly.

## Loop-state ledger

The wrapper records every round in a loop-state ledger (`--state-ledger <path>`, amendment A1): milestone, round, bound worktree, baseline, dirty-state digest, packet digest, PID, outcome, verdict, and finding ids. On any re-entry after context compaction, interruption, or restart, read the ledger first and resume from the recorded state rather than re-deriving it from memory. This is what keeps re-entry idempotent: a round already completed is not re-run, an accepted finding is not applied twice, and no duplicate reviewer is launched.

## Quota observation (Phase 6, observation-only)

The transport can optionally record pseudonymized before/after Codex quota snapshots around each review and doctor invocation, purely for observation. This is metadata only: quota data can never gate, block, cancel, retry, or reorder a review, and missing/ambiguous/incompatible data never changes a review outcome. Enable it with `--quota-observations <path-outside-the-worktree>` (and optionally `--quota-timeout`, default 5s, hard max 15s). After an observation window of at least 28 days and 30 intervals, the transport automatically produces one idempotent, non-authoritative evidence report (`decision_required`) recommending `no_action_warranted`, `continue_observation`, or `consider_advisory`. The report never enables a warning or alters review behavior without a separate decision. See [references/codex-quota-observations.md](references/codex-quota-observations.md).

## Use specialized skills when available

Inspect the active skill catalog rather than assuming a skill path or installation.

- When `$tdd` is available, invoke it for each milestone that changes production behavior or fixes a bug. Follow its public-interface, one-test-at-a-time red-green-refactor loop. The reviewed milestone plan supplies the behavior and interface context; honor any additional gates required by the installed skill.
- When `$systematic-debugging` is available, invoke it immediately for every bug, failed test or check, flake, timeout, performance regression, or unexpected result before proposing or applying a fix. Complete its root-cause, pattern, hypothesis, and implementation phases.
- Announce each specialized-skill invocation briefly so the user can see which workflow is active.
- If a skill is unavailable, continue without interruption using the embedded test-first or diagnostic steps below. Do not ask the user to install it, silently skip the discipline, or claim that it ran.
- If an installed specialized skill conflicts with repository instructions or explicit user requirements, follow the higher-priority instruction and record the adaptation.

These integrations are enhancements, not dependencies. Never delegate overall milestone ownership, Codex review handling, evidence reconciliation, documentation, or delivery cleanup to them.

## Prioritize Docker verification

When the project is Dockerized, prefer its documented Docker or Compose environment for service-backed integration, cross-process, worker, scheduler, migration, runtime-image, and end-to-end verification. Treat that environment as authoritative when behavior depends on container networking, installed system packages, image contents, entrypoints, volumes, service versions, or orchestration.

Fast host unit tests, lint, type checks, and other service-free checks may run outside Docker when repository policy permits. They supplement rather than replace required container verification. Never substitute a host-only server or mocked dependency for a workflow whose acceptance boundary is the Dockerized stack.

Run relevant Docker verification within each milestone before Codex review instead of deferring all container evidence to the final gate. Rebuild affected images when Dockerfiles, lockfiles, system dependencies, entrypoints, build arguments, or copied runtime artifacts change.

## Establish the contract

Before editing, derive and record:

- objective and authoritative specifications;
- observable acceptance criteria;
- invariants and failure behavior;
- non-goals;
- required tests, checks, and documentation;
- allowed git, publication, deployment, and destructive actions;
- human decision gates.

In the Lightweight profile this may be a single compact checklist; in the Standard and Safety-critical profiles keep it with the milestone plan.

Inspect the repository rather than asking questions whose answers are discoverable. Ask only when a missing decision would materially change behavior, risk, compatibility, or authority.

Record the initial branch, HEAD, worktree status, and pre-existing changes. Preserve user work. Claude is the sole production-artifact writer; Codex reviews read-only.

Estimate the affected components, state transitions, approximate code and test breadth, required services or containers, concurrency and recovery risks, documentation and operational changes, verification tiers, and cleanup obligations before editing.

## Maintain acceptance evidence

Create a compact acceptance-evidence ledger before implementation and update it after every meaningful verification run. Follow [references/evidence-ledger.md](references/evidence-ledger.md). In the Lightweight profile, one compact contract and checklist may replace a durable ledger.

Map every acceptance criterion to implementation, failure evidence, required real boundaries, documentation, and a precise status. Never treat "covered indirectly," "previously passed," "structurally implemented," or "expected to work" as fresh evidence.

Use an existing implementation report or project-prescribed artifact when one exists. Otherwise keep the ledger in task-local notes or an authorized durable artifact; do not add a tracked process document merely because this skill uses a ledger.

## Model invariants and failure behavior

In the Safety-critical profile—and whenever a milestone touches these risks—read and apply [references/safety-lifecycle.md](references/safety-lifecycle.md) before finalizing milestones.

Define authoritative and derived state, complete identities, persistence boundaries, ownership and ordering, retry behavior, corruption behavior, dependency failure, cleanup responsibility, and each meaningful crash window. Require an explicit recovery or rollback story before implementation.

Route the same safety-critical transition through one shared coordinator or transaction mechanism. Do not maintain separate API, CLI, scheduler, worker, or recovery implementations with merely similar semantics.

Keep inspection and read-only interfaces non-mutating. They must not silently repair, migrate, replay, reconcile, truncate, or clean up state. Prove non-mutation when durable or sensitive data is involved.

## Partition into reviewable milestones

Prefer vertical, independently verifiable behavior over layers. When a vertical slice would leave unsafe partial identity, fencing, locking, migration, or recovery behavior, implement a safely inert foundation first and keep outward wiring disabled until its invariants are proved. Each milestone must have:

- one coherent contract;
- a bounded diff that a reviewer can understand independently;
- focused tests proving success and important failures;
- documentation required to make that behavior truthful;
- explicit non-goals;
- a stable review baseline;
- an evidence-ledger update and truthful completion state.

Examples include one persistence identity, one recovery path, one API workflow, or one migration with its reader/writer behavior. Split a milestone when it needs more than three directed questions or the changed surface cannot be explained concisely.

Do not create checkpoint commits unless the user authorized commits. When commits are authorized, commit only after the milestone passes review and use that commit as the next baseline. Without commits, retain the original baseline and constrain later packets by exact files and symbols; disclose that the working-tree review is cumulative.

## Challenge the milestone plan

Run one Codex plan challenge before implementation when the profile requires it: required in the Safety-critical profile; in the Standard profile when dependency or sequencing risk applies; optional elsewhere. Skip it for trivial single-bounded changes unless the user requests it, and state the skip briefly.

Prepare a compact plan packet from [references/plan-packet.md](references/plan-packet.md) and validate it with `scripts/validate_review_packet.py`. Ask Codex to challenge decomposition and sequencing, not to design the implementation in advance. Focus on whether:

- each milestone is a coherent vertical slice with observable acceptance;
- global invariants are owned and proved at the earliest necessary milestone;
- dependencies, transitions, rollback/cutover needs, and integration gates are explicit;
- no later work relies on behavior that an earlier milestone leaves incomplete;
- milestone size permits meaningful independent review.

Use only one plan-review correction round by default. Classify Codex suggestions as `accept`, `disprove`, `defer`, or `escalate` using the same evidence rules as implementation findings. Incorporate accepted improvements, explain disproved or deferred suggestions compactly, and escalate architecture or authority decisions to the user. Then freeze milestone names, contracts, dependencies, and non-goals before editing production artifacts.

Plan review uses the same wrapper as milestone review; see [references/codex-cli-adapter.md](references/codex-cli-adapter.md). Invoke `scripts/run_codex_review.py review ...` with the plan packet, classify suggestions (`accept`/`disprove`/`defer`/`escalate`), apply accepted corrections, and re-review until a valid approval or the plan-review convergence rule. Never create fake tracked changes or commits to manufacture a review target, and never treat a turn-scoped stop-gate result as a plan review.

## Execute one milestone

For each milestone:

1. **Map** — inspect relevant callers, dependents, data flows, existing tests, and repository-required impact tooling.
2. **Test first** — invoke `$tdd` when available. Otherwise write one failing public-behavior test, confirm that it fails for the intended reason when feasible, implement only enough for green, and repeat one vertical behavior at a time. Never write the whole milestone's tests before implementation or refactor while red.
3. **Implement** — make the smallest coherent production, test, schema, configuration, and documentation changes.
4. **Verify focused behavior** — run the new test, failure cases, and load-bearing regressions. Refactor only while they remain green.
5. **Diagnose rigorously** — invoke `$systematic-debugging` when available before any fix. Otherwise reproduce the issue, read complete errors and recent changes, inspect evidence across component boundaries, compare a working pattern, isolate the failing boundary, form and minimally test one falsifiable hypothesis, add regression evidence, apply one root-cause correction, and rerun affected plus broader checks. After three failed fixes, stop and question the architecture with the user. Do not substitute retries or longer timeouts for a root cause.
6. **Verify required tiers** — run every applicable tier, Docker-first rule, and hermeticity check from [references/safety-lifecycle.md](references/safety-lifecycle.md), plus repository-mandated checks. For Dockerized projects, run the relevant containerized command before review and state the images, services, migrations, and critical boundaries exercised.
7. **Inspect architecture** — review the diff and tests against the post-green checklist in [references/safety-lifecycle.md](references/safety-lifecycle.md). Remove false claims, accidental scope, duplicated mechanisms, permissive fallbacks, temporary artifacts, and unsafe shortcuts.
8. **Reconcile evidence and docs** — update the acceptance ledger and current-state documentation with exact results, skips, limitations, and decisions.
9. **Prepare** — create a compact review packet from [references/review-packet.md](references/review-packet.md). Validate it with `scripts/validate_review_packet.py`.
10. **Request review** — invoke the Codex review wrapper directly with the milestone scope and the validated packet as review focus:

    ```
    python3 scripts/run_codex_review.py review \
      --review-kind milestone \
      --review-phase milestone|plan_challenge|correction|final \
      --worktree <abs-implementation-root> \
      --scope uncommitted|base|commit [--base <ref>|--commit <sha>] \
      --milestone <id> --round <n> \
      --packet <validated-review-packet> \
      --schema schemas/codex-review-output.schema.json \
      [--model <id|cli-default>] [--reasoning-effort <e>] [--codex-profile <p>] \
      [--require-config] \
      --output-dir <dir-outside-the-worktree> \
      --state-ledger <loop-state-ledger-path> \
      --doctor-receipt <matching-doctor-receipt> \
      [--quota-observations <dir-outside-the-worktree>/quota-observations.jsonl>]
    ```

    `--review-phase` is receipt/observation metadata only; it is never sent as a
    new verdict kind. `plan_challenge` requires `--review-kind plan`; `milestone`,
    `correction`, and `final` require `--review-kind milestone`. The optional
    `--quota-observations` enables Phase 6 observation-only quota capture
    (see [references/codex-quota-observations.md](references/codex-quota-observations.md)).

    For plan challenge use `--review-kind plan --scope uncommitted`; the plan target binds the packet digest, repository HEAD, and contents of explicitly referenced repository paths instead of pretending an implementation diff exists. The wrapper binds the exact worktree, runs Codex read-only with closed stdin and a bounded timeout, validates the structured verdict, writes a target-bound transport-v2 receipt, and records the round in the loop-state ledger. If preflight and doctor have not passed, stop.

The packet routes the reviewer's attention; it is not proof. The reviewer must inspect the repository diff and tests independently and remain read-only.

11. **Validate the verdict** — read the wrapper's receipt. Accept the milestone only on `outcome: completed` with a scoped `verdict: approve`. Treat `needs-attention` as input to the convergence loop in *Handle a blocked review*. Treat any transport outcome (`process_failed`, `timed_out`, `invalid_output`, `target_changed`, `cli_missing`, `cancelled`) as no verdict: record it and follow *Correction limits*; never retry an unchanged turn. A `completed` receipt is evidence the verdict is bound to this milestone's worktree, scope, baseline, and round, because the wrapper already rejected malformed, contradictory, and target-mismatched output.

## Increase reviewer independence

Claude picks the directed questions and can unintentionally constrain Codex to risks Claude already found. Every plan and milestone review therefore requires two passes, delivered as the review focus through the wrapper (the turn-scoped stop gate does not consume these passes):

- **Pass 1 — independent sweep.** The packet's independent-review mandate directs Codex to inspect the specification, diff, affected callers and flows, tests, and evidence on its own; to choose its own highest-risk attack surface; and to report material counterexamples, affected flows, or missing proof that Claude did not identify. Claude's directed questions must not constrain this pass.
- **Pass 2 — directed challenge.** Codex then answers zero to three optional directed questions supplied by Claude. "None" is valid; do not invent questions merely to fill the section.

Prefer counterexample-oriented directed questions when present, such as distinct logical states colliding under one identity, a partial write or replay duplicating, skipping, or combining effects, concurrency or lock loss violating the invariant, or tests that do not genuinely cross the claimed failure boundary.

## Handle a blocked review

Treat Codex findings—whether from the independent sweep or a directed question—as untrusted review input, not automatic instructions. Classify every finding by its effect:

- **accept** — supported by repository or test evidence and material to the contract; fix it.
- **disprove** — contradicted by concrete code or test evidence; include the concise evidence in the next review request. Passing tests alone are not enough to disprove a finding.
- **defer** — valid but explicitly outside the milestone and safe to defer; record the limitation where appropriate and explain it in the next review request.
- **escalate** — requires new authority, changes the approved design, or cannot be resolved safely within the milestone; stop and ask the user.

For each accepted finding, apply the smallest root-cause correction:

1. fix only the demonstrated defect and necessary tests/docs;
2. add or strengthen regression evidence;
3. rerun affected verification tiers;
4. update the evidence ledger, documentation, and packet with the correction and exact results;
5. invoke the wrapper again with the updated packet and the next round number (see [references/codex-cli-adapter.md](references/codex-cli-adapter.md)); do not retry unchanged turns or simulate a result.

Continue the correction-review loop automatically until a valid scoped `verdict: approve`. Do not stop after every review; stop only on approval or an escalation condition.

## Correction limits

Continue automatically through ordinary correction-review cycles. Escalate (stop and ask the user) when:

- the same material defect survives two correction reviews;
- the per-milestone **global cap** is reached: more than four total correction rounds, more than eight Codex invocations, or the configured spend ceiling. The per-defect rule above is not enough on its own, because Codex may raise a fresh material finding each round; the global cap bounds total cost and prevents unbounded convergence (amendment A2);
- Codex invalidates the approved architecture;
- a correction requires new product, security, migration, compatibility, or operational authority;
- transport failure or an invalid/stale result recurs and cannot be recovered safely.

Do not escalate merely because Codex produced actionable findings in the first review, and do not expand scope merely to satisfy optional reviewer suggestions.

## Advance between milestones

Once Codex returns a valid scoped `verdict: approve` for a milestone, stop at the milestone boundary and report the approved milestone. The stop belongs after Codex approval, not before review invocation. To continue, reread repository state, read the loop-state ledger, verify the accepted baseline and worktree ownership, and proceed to the next milestone without restating completed history. In operator-fallback mode (preflight failed, user-approved), each review and re-review is one operator command; never mistake a turn-scoped stop-gate `ALLOW` for approval, and never replace the boundary with manual copying of Codex reports. See [references/codex-cli-adapter.md](references/codex-cli-adapter.md).

When checkpoint commits are authorized, commit only an accepted, verified milestone and record its hash in the ledger. Never commit merely to manufacture a review target.

## Reconcile documentation

Update documentation throughout implementation, then reconcile it before the final gate:

- describe current behavior, interfaces, configuration, state ownership, degraded states, recovery, and rollback in current-system documentation;
- keep implementation history, evidence, corrections, and executed commands in the repository's designated history or implementation-report location;
- update diagrams when control flow, ownership, state, data, or deployment changes;
- update configuration examples and settings documentation together;
- remove stale claims and avoid mixing historical narrative into current-state documentation;
- ensure a new contributor or operator can understand normal operation and recovery without reading the conversation.

Follow repository conventions when they differ; do not invent a documentation taxonomy for projects that do not have one.

## Final integration gate

After all milestones are individually accepted:

1. reconcile every acceptance-ledger entry and unresolved finding;
2. run the complete repository-required and applicable tiered verification in a hermetic environment, prioritizing the documented Docker stack when the project is Dockerized;
3. inspect interactions across milestones, migrations, error paths, recovery, cleanup, and documentation;
4. prepare a final packet covering only cross-milestone risks and exact final results;
5. run the final integration review through the same autonomous convergence loop — invoke `scripts/run_codex_review.py review ...` with the final packet, address accepted findings, rerun verification, and re-review until a valid scoped `verdict: approve` (see [references/codex-cli-adapter.md](references/codex-cli-adapter.md));
6. perform only explicitly authorized delivery actions.

In the Lightweight profile, the single milestone review stands as the final integration review unless its findings expose broader risk. Do not claim completion while a material finding, required test, documentation update, cleanup obligation, or delivery action remains unresolved.

## Deliver and clean up conditionally

Perform commits, merges, pushes, pull requests, deployments, branch deletion, worktree removal, or resource cleanup only when authorized and applicable. Before delivery:

1. verify the source and destination branch/worktree state and divergence;
2. preserve unrelated changes and stop on ambiguous ownership;
3. ensure every acceptance criterion has a truthful evidence status;
4. reconcile skips, warnings, flakes, and limitations;
5. verify the delivered state again when merge, packaging, or deployment can change behavior;
6. refresh repository-required impact indexes or generated artifacts.

Before cleanup, inspect modified and untracked files and all isolated services or temporary resources. Never force-remove a dirty worktree, force-delete a branch, use destructive reset/clean as convenience, or delete resources with an unscoped selector. Confirm cleanup without touching pre-existing resources. If safe cleanup cannot be proved, leave the artifacts in place and report them.

## Token and loop discipline

- Never send Codex the conversation transcript or a chronological implementation diary.
- Keep each plan or implementation packet within the validator limit and include at most three directed questions; zero is valid.
- Point to files and symbols instead of pasting code or test output.
- Include exact commands and result summaries; omit routine exploration.
- Carry forward only unresolved constraints and findings.
- Keep ledger updates factual and compact; do not copy the ledger into every Codex packet.
- Use no more than two correction reviews per milestone (per-defect) before escalation, and never exceed the global cap of four rounds or eight invocations (amendment A2).
- Use no more than one plan-review correction before freezing the plan or escalating.
- Do not enable multiple concurrent review jobs or allow another writer in the worktree.
- Read the loop-state ledger on every re-entry so compaction never causes a double-applied correction, a duplicate reviewer, or a lost round (amendment A1).

## Boundary with Phase 4

This skill (`codex-reviewed-implementation`, Phase 5) and `codex-supervise-claude` (Phase 4) are inverse-direction companions. Here Claude writes and Codex reviews via the public Codex CLI; in Phase 4 Codex supervises and Claude writes via the public Claude-delegation surface. The two keep separate transport wrappers and opposite state ownership; they share only the structured-verdict schema shape and the packet-validator discipline. Do not let the two silently diverge in finding classification or convergence rules. See [references/codex-cli-adapter.md](references/codex-cli-adapter.md).

## Completion report

Lead with one truthful state:

- milestone complete, broader task incomplete;
- implemented but partially verified;
- complete and verified;
- blocked by a named authority or external condition.

Report:

- profile used and milestones delivered;
- independent review outcome, noting sweep versus directed findings;
- acceptance-ledger summary and exact verification results, skips, warnings, and flakes;
- architectural corrections made after green tests;
- documentation reconciled;
- git, branch, worktree, service, container, and temporary-resource state when applicable;
- authorized delivery actions performed;
- remaining limitations, or `None`.

Never claim completion from inferred, stale, skipped, failing, or flaky evidence.
