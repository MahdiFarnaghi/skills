# Portable safety and verification lifecycle

Apply sections proportionally to the milestone's risk and the repository's requirements.

## Contents

1. Invariant and failure model
2. Durable-transition protocol
3. Deterministic failure and concurrency evidence
4. Post-green architectural review
5. Verification tiers
6. Docker-first verification
7. Hermetic verification
8. Failure diagnosis

## 1. Invariant and failure model

Before changing safety-critical, durable, concurrent, distributed, migratory, security-sensitive, privacy-sensitive, or financial behavior, define:

1. authoritative state and derived state;
2. complete operation, transaction, tenant, and resource identity;
3. atomicity and persistence boundaries;
4. ownership, lock ordering, and maximum critical section;
5. generation, CAS, fencing, lease, or idempotency mechanism;
6. crash windows immediately before and after every durable mutation;
7. stale-worker, retry, restart, and multi-process behavior;
8. dependency-unavailable and ambiguous-outcome behavior;
9. malformed, duplicate, cross-scope, and conflicting-evidence behavior;
10. migration, compatibility, rollback, and cutover behavior;
11. read-only inspection semantics;
12. cleanup ownership and cleanup-failure behavior.

Reject partial identity comparisons, prefix-only trust, “latest event” heuristics, reread-and-compare-to-self checks, and query-failure-as-absence behavior when correctness depends on exact evidence.

## 2. Durable-transition protocol

For a durable transition, use this protocol or document and test an equivalent:

1. Capture authoritative state under the required ownership mechanism.
2. Release ownership for slow network, model, filesystem, checksum, or scoring work when safe.
3. Reacquire ownership and revalidate the complete snapshot.
4. Persist exact durable intent before the authoritative mutation when recovery requires it.
5. Apply the mutation with CAS, fencing, or an equivalent stale-writer defense.
6. Persist the committed effect or state transition.
7. Recover incomplete transitions by exact identity.
8. Make retry, replay, and cleanup independently idempotent.
9. Fail closed on missing, malformed, ambiguous, cross-scope, or conflicting evidence.

Keep unbounded or slow work outside distributed critical sections. Assert ownership immediately before and, where relevant, after each durable write.

## 3. Deterministic failure and concurrency evidence

Cover the applicable boundaries explicitly:

- success and idempotent retry;
- failure before durable intent;
- failure after intent and before mutation;
- failure after mutation and before committed evidence;
- restart recovery from every durable intermediate state;
- stale generation, lost lease, or fenced owner;
- identity or authoritative-state advancement during slow work;
- two-worker success/success ordering;
- two-worker success/failure ordering;
- dependency, lock, database, queue, or broker unavailability;
- malformed, duplicate, and conflicting evidence;
- cleanup failure and retry;
- response loss or ambiguous external outcome.

Use events, barriers, injectable boundaries, deterministic providers, or controlled subprocesses to force the intended interleaving. Do not use sleeps, short TTLs, timing luck, or scheduler luck as the primary proof. Timeouts may bound a test but must not create the race being claimed.

For each injected boundary, assert the exact durable state before replay, then replay and assert exact final effects, identities, counts, ownership release, and absence of unintended mutations.

## 4. Post-green architectural review

After focused tests pass, inspect for:

- duplicated coordinators or transition mechanisms;
- bypass paths, permissive fallbacks, or swallowed persistence failures;
- incomplete identity or tenant/resource scoping;
- prefix-based, first-row, `LIMIT 1`, or latest-event trust;
- broad coercion that normalizes malformed data;
- read-only paths that mutate, repair, replay, migrate, or clean up;
- slow work, deletion, or external calls under distributed locks;
- CAS or fencing followed by an unprotected install;
- cleanup that treats query failure as empty state;
- tautological, permissive, mock-only, or wrong-boundary tests;
- production hooks or configuration added only to make tests pass;
- local environment leakage and unscoped external resources;
- stale documentation, diagrams, configuration examples, or error taxonomy.

Green tests do not waive this review. Add corrective regression evidence for every material finding.

## 5. Verification tiers

Run every tier required by the acceptance criteria, changed boundaries, and repository instructions:

1. **Focused:** changed units, failure injection, identities, ownership, and direct regressions.
2. **Full local:** full non-integration suite, lint, format check, typecheck, build, and static analysis as applicable.
3. **Real service:** real database, cache, queue, filesystem, external API test double, or other stateful dependency.
4. **Cross-process:** real subprocesses for leases, crashes, fencing, restart recovery, signals, or process-local state.
5. **Deployment-shaped:** containers, packages, migrations, volumes, workers, schedulers, or runtime images when those boundaries matter.
6. **Operator surface:** authenticated and unauthenticated HTTP, CLI exit codes, telemetry, logs, alerts, and operational recovery when applicable.

State which critical boundaries are real for every scenario. Do not call a test integration, cross-process, deployment-shaped, or end-to-end when its load-bearing boundary is mocked or bypassed.

If a required tier cannot run, mark the relevant acceptance criterion `partially_verified` or `blocked` and state the exact missing evidence.

## 6. Docker-first verification

When the repository contains and documents a Dockerized development or deployment environment:

- prefer the documented Docker or Compose commands for service-backed integration, cross-process, migration, worker, scheduler, runtime-image, and end-to-end tests;
- apply required schema, bootstrap, fixtures, and health checks inside the isolated stack using the project's supported procedure;
- test through the same container entrypoints, service discovery, networks, dependency versions, and mounted or named-volume behavior that the workflow uses;
- rebuild images when Dockerfiles, dependency locks, build inputs, entrypoints, system packages, or copied runtime artifacts change;
- do not replace container acceptance with host-only processes, in-memory services, or mocks when the container boundary is load-bearing;
- allow host unit tests, lint, static analysis, and service-free checks as fast feedback, but still execute the applicable Docker tier before acceptance;
- state the Compose project, services, images, migrations, ports, networks, and volumes actually used;
- use isolated project names, ports, credentials, databases, networks, and volumes rather than the default developer or production stack;
- verify teardown and absence of owned containers, networks, volumes, and temporary data without touching pre-existing resources.

If Docker is unavailable or the documented stack cannot run, report the exact reason and mark affected criteria `partially_verified` or `blocked`. Do not infer Docker success from host tests.

## 7. Hermetic verification

- Declare material environment variables explicitly.
- Detect developer configuration files that alter defaults; isolate or override intentionally without deleting them.
- Use unique database names, key prefixes, temporary directories, project names, ports, networks, volumes, tenants, and external identifiers.
- Guard against development or production data mounts and default credentials.
- Preserve and report pre-existing processes, services, containers, and resources.
- Inject clients and endpoints explicitly when the project supports it.
- Make teardown failure visible when resource queries fail or owned resources remain.
- Never use unscoped destructive cleanup.
- Confirm main or pre-existing resources are unchanged when isolation is an acceptance concern.

## 8. Failure diagnosis

For any failure, flake, timeout, or unexpected result:

1. Read the complete failure, logs, and relevant state.
2. Reproduce the exact symptom consistently when possible.
3. Gather evidence at every boundary rather than guessing from the final error.
4. Compare with a known working path or invariant.
5. Form one falsifiable root-cause hypothesis.
6. Add a regression or diagnostic test that distinguishes the hypothesis.
7. Apply the smallest coherent correction.
8. Rerun the affected scenario and broader load-bearing suites.

Do not label a retry, sleep, broader exception handler, or longer timeout as the root-cause fix unless that behavior is explicitly part of the contract.
