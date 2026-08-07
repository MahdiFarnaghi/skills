# Phase 3 — Machine-enforced safety gates

## Outcome

Replace the Safety-critical profile's advisory, model-reported gates with a
machine-enforced workflow. A model may request a transition and may read the
result, but it must not be able to create, modify, reinterpret, or silently skip
the authoritative evidence that permits the next transition.

Phase 3 is complete when a Safety-critical run cannot legitimately reach
`completion_allowed` unless the required plan challenge, bounded implementation
baseline, witnessed test-first red–green evidence, verification tiers,
independent review, documentation reconciliation, and cleanup checks have
produced fresh passing evidence for the exact reviewed revision. Missing, stale, malformed, timed-out, skipped, or failing evidence must
produce a non-passing state with a specific reason.

## Scope

This phase applies first to `skills/codex-reviewed-implementation` and its
Safety-critical profile. The design should remain extensible to Standard and
Lightweight profiles, but their policy is not changed unless explicitly covered
by an acceptance criterion below.

In scope:

- a documented threat and authority model;
- a versioned gate-policy, workflow-state, and evidence schema;
- semantic validation of gate state and evidence relationships;
- a trusted runner that executes gates and writes authoritative results;
- machine-enforced test-first (red–green) evidence for Safety-critical milestones;
- lifecycle hooks that consult trusted state before edits and completion;
- automated positive, negative, tampering, replay, timeout, and bypass tests;
- updates to the skill, adapter, evidence-ledger, and safety-lifecycle docs.

Out of scope:

- proving that an unconstrained machine administrator cannot bypass controls;
- treating model-authored Markdown, command transcripts, or exit-code text as
  authoritative evidence;
- signing or remote attestation unless local filesystem and process isolation
  cannot establish the required boundary;
- forcing checkpoint commits when an immutable snapshot or exact baseline/HEAD
  pair can provide the same bounded-review guarantee;
- changing the independent reviewer or allowing it to write production files.

## Global invariants

1. **Separate proposal from evidence.** Model-authored state is untrusted input.
   Only the trusted runner may write authoritative gate results.
2. **Bind evidence to scope.** Every result is bound to the repository identity,
   worktree, profile, task/run ID, policy version, baseline, target revision or
   snapshot, command/scenario, and relevant environment.
3. **Fail closed.** Missing, unknown, malformed, stale, skipped, failed,
   cancelled, or timed-out results never satisfy a required gate.
4. **No status laundering.** `attempted`, `running`, and `passed` are distinct.
   A later summary cannot convert a non-passing result into a pass.
5. **No mutable-pass reuse.** A production change after verification or review
   invalidates every downstream gate whose evidence was bound to the old target.
6. **Bounded review target.** Each review uses an immutable, reproducible target:
   preferably an accepted baseline and target commit, otherwise a content-addressed
   snapshot or patch manifest. A cumulative working tree is not sufficient for a
   mandatory Safety-critical review.
7. **One-way workflow.** State advances only through validator-approved
   transitions. Re-running or superseding a gate creates a new result; it does
   not rewrite history.
8. **Independent review remains read-only.** The review transport cannot modify
   production artifacts or author the evidence it is judging.
9. **Truthful claims.** Hooks authorize lifecycle transitions, not the semantic
   truth of arbitrary prose. Documentation must state this boundary precisely.
10. **Preserve user work.** Enforcement and cleanup must not mutate unrelated
    files, branches, worktrees, containers, volumes, or services.
11. **Test-first evidence is ordered and separately witnessed.** A genuine
    red—a test that fails for the intended reason—must precede green for the same
    behavior, and both must be produced by the trusted runner and bound to the
    same target. Green with no matching red, a red with no following green, a red
    for the wrong reason, or implementation that predates its red are each
    non-passing. The model cannot supply the failure reason; only the runner may
    classify it.

## Delivery sequence

Complete the six workstreams in order. Steps 1 and 2 freeze the contract; Steps
3 and 4 implement the decision engine and trusted evidence producer; Step 5
connects enforcement to the agent lifecycle; Step 6 attempts to defeat the
whole system before rollout.

## 1. Define the threat and authority model

### Objective

State exactly which actors, files, processes, and configuration surfaces are
trusted, which are model-controlled, and which attacks Phase 3 claims to prevent.
Do this before choosing a file location or hook implementation.

### Work

1. Inventory every relevant actor and capability:
   - Claude/model tool access, including shell, Edit, Write, and configuration
     changes;
   - the Claude Code hook host and hook configuration ownership;
   - the Codex review plugin, its review-result format, timeout behavior, and
     writable paths;
   - the new gate runner and validator;
   - the user/operator and repository processes such as Git and Docker;
   - CI, if it will provide a stronger enforcement boundary later.
2. Build an authority matrix covering read, create, update, delete, execute, and
   configure permissions for:
   - gate policy and schema;
   - proposed run requests;
   - authoritative results and transition state;
   - hook configuration;
   - review artifacts;
   - verification logs;
   - repository and Git metadata.
3. Define the adversary as a completion-driven but non-administrative model that
   may omit steps, write plausible-looking files, replay old results, alter the
   worktree after a pass, invoke commands outside the intended wrapper, or try to
   disable model-writable controls.
4. Document trusted-computing-base assumptions. At minimum, the gate runner,
   validator, policy, authoritative result store, and active hook configuration
   must not be writable through the model's ordinary tool authority. If the local
   runtime cannot enforce that condition, record the design as tamper-evident or
   advisory—not tamper-proof—and select a stronger boundary such as an operator
   process or CI check.
5. Define the guarantee boundary:
   - pre-edit hooks can block covered production-edit tools;
   - pre-tool hooks cannot claim to cover an unobserved write mechanism;
   - stop hooks can block turn completion based on state;
   - no hook can prove the truth of unrestricted natural-language claims;
   - administrative users remain able to override controls explicitly.
6. Record an override policy. Overrides require a user/operator action, reason,
   timestamp, exact scope, and an explicit non-passing/waived result. The model
   cannot issue or silently infer an override.
7. Add a design decision record or equivalent reference documenting the chosen
   boundary, rejected alternatives, residual risks, and conditions that would
   require migration to CI or another external orchestrator.

### Deliverables

- threat-model document with assets, actors, trust boundaries, abuse cases, and
  residual risks;
- capability/authority matrix;
- explicit security and non-security claims;
- override and recovery policy;
- decision on the authoritative storage and execution boundary.

### Exit criteria

- every authoritative artifact has one named producer and a permission model;
- the model cannot write the authoritative store or active enforcement config
  through its normal tools, or the limitation is explicitly classified as
  advisory and blocks claims of hard enforcement;
- replay, fabrication, stale-evidence, post-pass mutation, hook-disablement,
  alternate-write-path, timeout, and partial-write threats each have a planned
  prevention or a documented residual risk;
- a reviewer can state exactly what Phase 3 does and does not guarantee.

## 2. Define the workflow-state and evidence schema

### Objective

Create a versioned, machine-readable contract for policy, run identity,
transitions, gate attempts, authoritative results, supersession, and completion.
The schema must make invalid and ambiguous states unrepresentable where
practical and reject them otherwise.

### Work

1. Separate three document types:
   - **policy**: profile-specific required gates and transition prerequisites;
   - **run manifest**: immutable identity and scope of one workflow run;
   - **gate result/event**: append-only output produced by the trusted runner.
2. Define the initial Safety-critical state machine:

   ```text
   initialized
       -> contract_recorded
       -> plan_challenge_passed
       -> edits_allowed
       -> milestone_test_first_passed
       -> milestone_verified
       -> milestone_review_passed
       -> milestones_accepted
       -> final_verification_passed
       -> final_review_passed
       -> docs_and_cleanup_reconciled
       -> completion_allowed
   ```

   Model failure, timeout, cancellation, blocked, waived, invalidated, and
   superseded as explicit result states rather than successful transitions.

   The `edits_allowed` state permits creating failing tests; it does not permit
   production-tree edits for a behavior until the trusted runner has witnessed a
   red event for that behavior. `milestone_test_first_passed` is reached only
   when at least one runner-witnessed red has been followed by a matching green
   for the same target, and it is a prerequisite of `milestone_verified`.
3. Define required run identity fields:
   - schema and policy versions;
   - unique run and task IDs;
   - repository canonical path or stable repository ID;
   - worktree identity and branch;
   - initial HEAD and initial dirty-state manifest;
   - selected profile;
   - creation time and trusted producer identity.
4. Define required gate-result fields:
   - unique event and attempt IDs;
   - gate type and milestone ID, when applicable;
   - `requested`, `started`, and `finished` timestamps;
   - outcome from a closed enum such as `passed`, `failed`, `timed_out`,
     `cancelled`, `blocked`, `skipped`, `waived`, or `error`;
   - process exit status and termination reason where a process ran;
   - exact baseline and target identity;
   - content digest of the reviewed or verified snapshot/manifest;
   - normalized command or scenario identifier and arguments;
   - environment/boundary declaration, including Docker services and image
     digests when applicable;
   - locations and digests of logs and review reports;
   - for `test_first` gates: behavior or test identifier, observed red outcome
     and runner-classified failure reason, production-target digest captured at
     red time, observed green outcome, and production-target digest captured at
     green time, with the red event referenced as predecessor of the green;
   - runner and validator versions;
   - predecessor event and superseded-event references;
   - structured diagnostics safe for hooks to display.
5. Define evidence freshness and invalidation rules. Specify which repository
   changes invalidate plan acceptance, focused verification, Docker verification,
   milestone review, final verification, documentation, and cleanup evidence.
   Treat ambiguity as invalidation.
6. Define the bounded-target representation:
   - accepted baseline commit plus target commit; or
   - immutable snapshot/patch manifest listing paths, modes, object/content
     hashes, and relevant untracked files.
   The representation must detect edits made after a passing run.
7. Define profile policies declaratively. Each gate specifies prerequisites,
   allowed outcomes, target-binding rules, freshness, whether an operator waiver
   is permitted, and which later gates it unlocks.
8. Publish JSON Schema files with `additionalProperties: false` where forward
   compatibility permits, closed enums, strict formats, and explicit versioning.
   Include canonical valid and invalid fixtures.
9. Decide how events are made tamper-resistant within the selected boundary:
   permissions plus atomic creation at minimum; optionally a hash chain or
   signatures for tamper detection across copied stores. Cryptographic fields
   must not be presented as protection if the model can access the signing key.
10. Map the existing Markdown acceptance ledger to derived human-readable output.
    Markdown may summarize authoritative JSON, but must not override it.

### Deliverables

- `gate-policy` JSON Schema and Safety-critical policy instance;
- `run-manifest` JSON Schema;
- `gate-event`/result JSON Schema;
- state-transition specification and invalidation table;
- valid, invalid, stale, and superseded fixtures;
- mapping from authoritative state to the existing ledger statuses.

### Exit criteria

- schema validation rejects unknown outcomes, missing scope, missing target
  binding, malformed timestamps/digests, incomplete process results, and
  ambiguous Docker/review declarations;
- no `attempted`, `running`, `skipped`, `waived`, `timed_out`, or `error` event can
  satisfy a passing prerequisite;
- the schema can represent every current evidence-ledger status without
  collapsing execution outcome and acceptance status;
- a changed target digest makes earlier verification and review ineligible;
- schema versions and migration/compatibility behavior are documented.

## 3. Implement the semantic validator and decision engine

### Objective

Extend validation beyond document shape. Given a policy, immutable run manifest,
append-only events, and current repository state, compute the only valid current
state and explain every unsatisfied prerequisite.

### Work

1. Keep `scripts/validate_review_packet.py` focused on compact Markdown packet
   structure. Add a separate gate validator/decision module with a small CLI so
   packet validity cannot be mistaken for gate success.
2. Implement validation in layers:
   - JSON Schema validation;
   - policy and schema-version compatibility;
   - run and repository identity validation;
   - event uniqueness, ordering, predecessor, and supersession validation;
   - state-transition validation;
   - target digest and Git/snapshot reconciliation;
   - freshness and invalidation evaluation;
   - required-gate coverage for the selected profile;
   - final decision and stable reason codes.
3. Compute state from events; do not trust a writable `current_state` field.
   Reject impossible transitions, duplicate terminal results, unknown gates,
   cross-run events, future timestamps beyond a documented tolerance, broken
   event chains, and results produced by an unapproved runner.
4. Add exact semantics for process-backed evidence:
   - `passed` requires normal completion and the gate-specific success criteria;
   - timeout, signal, cancellation, missing exit status, truncated result, runner
     crash, or unreadable artifact is non-passing;
   - test counts and warnings are recorded without allowing a zero-test or
     all-skipped run to pass unless the gate policy explicitly permits it;
   - review success requires the adapter's documented clean result, not merely a
     report-file presence or process exit zero;
   - test-first `passed` requires a runner-witnessed red for the intended
     behavior before a matching green, identical target binding, a production
     change between red and green, and the runner's own failure classification; a
     green with no preceding red, a red with no following green, a red classified
     as a collection, import, or timeout failure, a red and green on different
     targets, or an identical production digest at red and green is non-passing.
5. Recompute the current target identity during validation. Detect dirty files,
   untracked files in scope, changed Git metadata, baseline mismatch, or content
   changes after a passing event and return `invalidated` with affected gates.
6. Produce two outputs:
   - a machine result with `allowed`, computed state, stable reason codes,
     invalidated events, and next eligible actions;
   - a concise human explanation suitable for hook feedback and the ledger.
7. Add a read-only command to answer specific decisions such as
   `can-edit-production`, `can-request-review`, `can-advance-milestone`, and
   `can-complete`. Use distinct exit codes for allowed, denied, malformed input,
   and internal error; default every unexpected condition to denied. For a
   Safety-critical profile, `can-edit-production` denies a production-tree edit
   whose target behavior has no runner-witnessed red event, with a distinct
   reason code; behavior-to-path coverage is best-effort and its residual gap is
   documented under Step 5.
8. Generate or update the Markdown ledger from validated events without allowing
   the generated view to become an input to authorization.
9. Unit-test every transition, outcome, invalidation rule, and reason code.
   Property-test event ordering and malformed combinations if the project can do
   so without adding disproportionate dependencies.
10. Document CLI contracts and ensure diagnostics never expose secrets captured
    from the verification environment.

### Deliverables

- semantic validation/decision library;
- read-only validator CLI;
- derived ledger renderer or mapping layer;
- comprehensive unit and fixture tests;
- updated packet validator documentation clarifying its limited role.

### Exit criteria

- the validator deterministically derives the same state independent of event
  file enumeration order;
- every denial has a stable, actionable reason code;
- malformed input, I/O failure, repository mismatch, and internal exceptions fail
  closed with a non-zero exit;
- mutation after a pass invalidates the correct downstream gates;
- no model-authored Markdown or claimed command result affects authorization;
- tests cover all state transitions and every non-passing outcome.

## 4. Implement the trusted gate runner and authoritative evidence store

### Objective

Ensure authoritative evidence is produced from actual execution by a component
outside ordinary model write authority. The runner, not the model, captures
process termination and writes the final event atomically.

### Work

1. Choose and provision the boundary selected in Step 1. Preferred order:
   - operator/host-managed local service or wrapper with a non-model-writable
     executable, policy, configuration, and result directory;
   - CI job with protected configuration and artifact/status reporting;
   - local tamper-evident mode only when stronger isolation is unavailable, with
     guarantees labeled accordingly.
2. Expose a narrow request interface. The model may select an allowed gate and
   run/milestone ID, but may not supply arbitrary commands, result paths, success
   predicates, runner identity, or policy overrides. Commands come from trusted
   policy or repository-owned approved configuration.
3. On each request, the runner must:
   - validate the run and current prerequisites before starting;
   - resolve the exact repository, worktree, baseline, target, and snapshot;
   - allocate a unique attempt ID and isolated output directory;
   - capture start time and environment/boundary metadata;
   - execute without shell interpolation unless explicitly required and safely
     encoded;
   - capture stdout/stderr, real exit status, signal, cancellation, and timeout;
   - collect gate-specific structured results;
   - compute artifact and target digests;
   - write logs first and the terminal event last using atomic create/rename;
   - return the validator's recomputed decision rather than self-declaring the
     next workflow state.
4. Add gate adapters for:
   - plan challenge result;
   - focused and repository-required checks;
   - real-service, cross-process, and Docker verification;
   - independent Codex milestone and final review;
   - test-first red and green observation, capturing the production-target digest
     at red time and classifying the failure reason itself rather than accepting a
     model-supplied reason;
   - documentation reconciliation;
   - scoped cleanup verification.
5. Define review-adapter parsing against a pinned, verified plugin contract.
   A missing report, incompatible plugin version, timeout, invocation error,
   review finding requiring correction, or unrecognized result is non-passing.
6. Ensure Docker evidence records the Compose project, services, image IDs or
   digests, migrations, networks, volumes, and teardown status. Never infer a
   container pass from host-only tests.
7. Isolate concurrent runs with per-run locks and unique resources. Reject a
   second writer for the same run and milestone; do not resolve concurrency using
   last-writer-wins.
8. Make results append-only to the model. Define retention, archival, crash
   recovery, orphan-attempt handling, and safe operator reset procedures.
9. Treat credentials and environment carefully: allowlist captured metadata,
   redact logs at source where possible, restrict file permissions, and never
   serialize secrets into evidence.
10. Add health/version reporting so hooks can distinguish runner unavailable,
    incompatible, busy, and denied states without treating any as success.

### Deliverables

- trusted runner/service or CI implementation;
- narrow gate-request client;
- authoritative append-only result store;
- adapters for verification and Codex review;
- atomicity, locking, redaction, recovery, and retention documentation;
- operator installation and diagnostic instructions.

### Exit criteria

- the model's normal file and shell tools cannot manufacture or modify an
  accepted event within the stated threat model;
- a killed runner leaves no passing partial event and is recoverable;
- actual exit status and timeout state come from the runner process supervisor;
- concurrent duplicate requests cannot create conflicting accepted results;
- stale or post-mutation results are rejected by a fresh validator decision;
- plugin, Docker, and dependency unavailability remain visibly non-passing.

## 5. Add lifecycle hooks that enforce validator decisions

### Objective

Connect the decision engine to Claude Code's lifecycle so covered production
edits and turn completion are denied until their prerequisites are satisfied.
Hooks must consult authoritative state; they must never parse narrative claims or
accept the existence of a model-writable file as proof.

### Work

1. Verify the installed Claude Code hook API, event payloads, exit semantics,
   timeout behavior, recursion behavior, and configuration precedence. Pin the
   supported version range and fail visibly outside it.
2. Install hook configuration through an operator-controlled mechanism. The
   active configuration and hook executable must be outside ordinary model write
   authority. If this cannot be guaranteed, label the hooks advisory and do not
   claim that bypass is impossible.
3. Add a production-edit precondition hook for covered `Edit`, `Write`, notebook,
   and patch operations. It must:
   - identify the target repository and path from the actual tool payload;
   - distinguish production scope from authorized plan/evidence-request paths;
   - treat test-file edits as permitted once `edits_allowed`, but treat
     production-tree edits as additionally requiring a runner-witnessed red event
     whose target covers the edited path, so implementation cannot be written
     before its red;
   - call `can-edit-production` for the active run;
   - deny when no unique active run exists, prerequisites are incomplete, the
     validator or runner is unavailable, the path cannot be classified, or a
     production edit lacks a matching red event;
   - return the precise missing prerequisite and permitted next action.

   Mapping an arbitrary edit to the behavior a red was declared for is
   approximate; classify any production write the hook cannot bind to a witnessed
   red as a residual limitation and document it, rather than silently permitting
   it.
4. Cover shell-mediated writes. Prefer a runtime permission boundary that makes
   protected configuration/evidence unwritable regardless of command. For
   production-tree writes, either enforce an approved-command wrapper/sandbox or
   document that `PreToolUse` command-text inspection is incomplete. Do not claim
   complete write mediation based on brittle shell-string matching.
5. Add pre-review and milestone-advance checks so review cannot start against an
   unverified or unbounded target, later milestones cannot silently begin while
   the previous milestone remains unaccepted, and a milestone cannot advance past
   `edits_allowed` to verification or review without `milestone_test_first_passed`.
6. Add a Stop hook that calls `can-complete`. It blocks normal completion unless
   the computed state is `completion_allowed` for the current target. It must not
   search prose for words such as "done"; it enforces the workflow boundary for
   the Safety-critical run as a whole.
7. Define non-recursive blocking behavior. Hook denial must provide a bounded
   diagnostic, avoid infinite stop loops, and distinguish actionable missing
   gates from operator-required recovery. Emergency override remains an explicit
   operator action recorded under Step 1's policy.
8. Ensure changes after review trigger immediate invalidation. The next hook
   decision must deny milestone advancement/completion and name the gates that
   must be rerun.
9. Integrate hook enablement verification into the skill's Safety-critical
   preconditions. Refuse to advertise the machine-enforced profile when hook
   version, config ownership, runner health, or evidence-store permissions do not
   match the documented assumptions.
10. Update `SKILL.md`, the plugin adapter, evidence-ledger reference, and safety
    lifecycle so the documented workflow matches actual enforcement and retains
    a clear operator fallback.

### Deliverables

- operator-owned hook configuration and hook entrypoints;
- edit, review, milestone-advance, and completion enforcement;
- hook compatibility/health check;
- concise denial and recovery messages;
- updated skill and reference documentation.

### Exit criteria

- production edits are denied before plan acceptance in every covered write path;
- review is denied without fresh required verification and an immutable bounded
  target;
- completion is denied for missing, skipped, failed, timed-out, malformed,
  superseded, or invalidated required evidence;
- hook/validator/runner failure denies rather than permits;
- active enforcement cannot be disabled using the model's ordinary authority;
- documentation explicitly lists any write mechanisms the hooks cannot mediate.

## 6. Run adversarial bypass and end-to-end acceptance testing

### Objective

Demonstrate that the composed system resists realistic omission, fabrication,
replay, mutation, concurrency, and failure attempts. Test the enforcement claim,
not merely the happy-path implementation.

### Work

1. Build a disposable fixture repository with a Safety-critical policy, a small
   production file, focused tests, a Docker-shaped gate where available, a fake
   review adapter, and isolated evidence storage. Never run destructive tests
   against a developer's real repository or Docker resources.
2. Add a full happy path proving every valid state transition and showing that
   the correct next action becomes available only after its prerequisites pass.
3. Add omission cases:
   - edit before plan challenge;
   - production-tree edit for a behavior before its witnessed red;
   - review before required verification;
   - advance before review acceptance;
   - milestone advance or review with no `test_first_passed` evidence;
   - completion with missing documentation or cleanup evidence;
   - completion after only host tests when Docker is required.
4. Add result-integrity cases:
   - hand-written fake result and fake success log;
   - copied result from another run/repository/worktree;
   - replayed result from an earlier target;
   - modified event, log, report, or manifest;
   - missing or mismatched digest;
   - broken predecessor/hash chain;
   - result from an unapproved runner or incompatible version;
   - green event with no preceding red, or red with no following green;
   - red and green bound to different targets, or a replayed/cross-run red;
   - red classified as a collection, import, or timeout failure;
   - identical production-target digest at red and green (no real change);
   - red event whose failure reason was model-supplied rather than runner-classified.
5. Add lifecycle cases:
   - modify a production file after verification;
   - modify after milestone or final review;
   - add an in-scope untracked file after snapshot;
   - switch branch, HEAD, worktree, or baseline;
   - start two attempts concurrently;
   - reuse an accepted milestone event for another milestone.
6. Add process-failure cases:
   - non-zero command exit;
   - zero tests collected or all tests skipped;
   - timeout, signal, cancellation, and runner crash;
   - truncated or partially written log/event;
   - validator crash or unreadable store;
   - Codex plugin unavailable, timed out, malformed, or reporting material
     findings;
   - Docker daemon/service/health-check/teardown failure.
7. Add hook-bypass cases:
   - direct Edit/Write/Patch attempts;
   - shell redirection and script-mediated production writes;
   - attempts to alter policy, schemas, hook config, runner, or evidence;
   - stop with no active run, ambiguous runs, or incomplete gates;
   - repeated Stop-hook invocation and emergency override handling.
   Classify any technically unmediated mechanism as a residual limitation unless
   the filesystem/runtime boundary blocks it independently.
8. Add authorization and cleanup cases:
   - model-authored waiver or override;
   - operator-scoped waiver with correct audit data;
   - cleanup query failure;
   - pre-existing containers/resources present;
   - unscoped cleanup request.
9. Run compatibility tests for every supported Claude Code and Codex plugin
   version. An unsupported version must fail the preflight rather than silently
   falling back to advisory behavior.
10. Produce an acceptance matrix mapping each global invariant and threat-model
    abuse case to a deterministic test and observed result. Have an independent
    reviewer inspect both the implementation and the test harness for shared
    assumptions or fake boundaries.

### Deliverables

- isolated end-to-end fixture and harness;
- positive, negative, tampering, replay, concurrency, crash, and hook tests;
- compatibility matrix;
- invariant-to-test acceptance matrix;
- residual-risk and rollout report.

### Exit criteria

- every Global invariant has at least one passing positive test and one relevant
  denial/invalidation test;
- all listed bypass attempts either fail deterministically or appear as explicit
  residual risks that narrow the enforcement claim;
- no test relies on prose interpretation, timing luck, or a model-writable file
  as authoritative proof;
- test teardown proves that pre-existing files, branches, processes, containers,
  networks, and volumes remain untouched;
- the final independent review reports no unresolved material enforcement flaw;
- the Safety-critical documentation uses only guarantees demonstrated by the
  acceptance suite.

## Rollout and migration

1. Ship the schema and validator behind an opt-in experimental policy.
2. Run it in observe-only mode on representative Safety-critical workflows to
   find schema gaps and false denials. Observe-only output must never be described
   as enforcement.
3. Enable trusted evidence production while existing Markdown ledgers remain a
   generated or parallel view.
4. Enable edit and review gates for a small set of fixture or pilot repositories.
5. Enable the Stop gate only after recovery, override, and infinite-loop behavior
   have passed end-to-end tests.
6. Make machine-enforced Safety-critical the default only when preflight proves
   the runner, store, hooks, and plugin versions satisfy the authority model.
7. Preserve an explicit legacy/advisory mode for unsupported environments, but
   ensure its completion report says that machine enforcement was unavailable.

## Phase 3 definition of done

- all six workstreams meet their exit criteria;
- the Safety-critical policy and schemas are versioned and documented;
- the semantic validator and trusted runner pass their focused and end-to-end
  suites;
- hooks fail closed and are installed outside model write authority;
- authoritative results bind to the exact repository target and real execution;
- mutation, replay, fabrication, timeout, skipped checks, and review failure all
  block downstream transitions;
- green without a runner-witnessed red, implementation before its red, and
  red-for-the-wrong-reason each block test-first and the transitions that depend
  on it;
- the acceptance ledger is derived from or reconciled against authoritative
  events rather than trusted as prose;
- the skill and all affected references describe the implemented behavior and
  residual limitations accurately;
- the independent final review is clean, and there are no unresolved material
  findings, unexplained skips, unsafe cleanup obligations, or unsupported
  completion claims.
