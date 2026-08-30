# Portable safety and verification routing

Use Standard for substantial work and Safety-critical for security, privacy,
financial, durable-state, distributed, concurrency, recovery, migration,
compatibility, or high-impact operational changes. This reference is
self-contained; it does not rely on another skill's private transport rules.

For Standard, maintain a compact acceptance ledger and plan packet when
dependencies or sequencing matter. For Safety-critical, require the full
ledger, plan challenge, failure/concurrency model, rollback or recovery plan,
real-boundary verification, and final integration review. In either profile,
Terra is the only completion authority.

Before implementation, state authoritative versus derived state, complete
identity, ownership and lock ordering, persistence boundaries, generation/CAS/
fencing or idempotency, crash windows, stale-worker and retry behavior,
dependency failure, malformed/duplicate/conflicting evidence, migration and
rollback behavior, read-only semantics, and cleanup ownership. Fail closed on
missing, malformed, cross-scope, or conflicting evidence.

Verify the changed boundary in increasing order as applicable: focused unit and
failure tests; full local checks; real service dependencies; cross-process
restart/crash behavior; deployment-shaped containers/migrations; and operator
surfaces such as CLI exit codes and recovery paths. When a container boundary
is load-bearing, use the documented isolated Compose/project setup and report
its services, volumes, migrations, health checks, and teardown. Do not call a
mocked test integration or end-to-end proof. If a required tier cannot run,
mark the criterion partially verified or blocked with the exact reason.

After green tests, inspect for bypass paths, permissive fallbacks, incomplete
identity, unprotected installs after CAS/fencing, mutating read-only paths,
slow work under locks, unsafe cleanup, wrong-boundary tests, and stale docs or
configuration. Add a regression test for every material finding. Preserve
unrelated changes and perform delivery actions only when explicitly authorized.
