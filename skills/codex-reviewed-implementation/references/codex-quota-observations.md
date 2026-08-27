# Codex quota observations (Phase 6, observation-only)

This reference documents the **observation-only** quota instrumentation added to
the Phase 5 transport. It records pseudonymized before/after quota snapshots
around each Codex review and doctor invocation, purely for observation. Quota
data is **never load-bearing**: it cannot gate, block, cancel, retry, defer, or
reorder a review, and missing/ambiguous/incompatible data must never change a
review outcome.

The observation design follows the skill's requirement that quota data remain
that the Codex quota RPC (`account/rateLimits/read`, exposed by the experimental
`codex app-server`) is a semantically weak, still-churning signal: `usedPercent`
is a coarse integer, the weekly window cannot be reliably identified, and a
machine-enforced "preserve 20% reserve" invariant is unenforceable from a
one-shot CLI. So instead of enforcing anything, we observe first and decide
later.

## What it is not

- Not a gate. No admission control, no reservations, no single-use receipts, no
  emergency SIGINT, no durable blocked-state machine, no resumption logic.
- Not attribution. A candidate delta is interval *correlation*, not a measurement
  of how much a specific review consumed.
- Not policy. The generated report is `decision_required` evidence. It can
  recommend, but it can never enable a warning, change thresholds, or alter
  review behavior without a separate user decision and implementation change.

## How it works

For each claimed review round (and each doctor call):

1. After `claim_round`, attempt one bounded **before** quota read.
2. Always launch the review regardless of the before result.
3. Finalize the original review outcome independently.
4. From `finally`, attempt one bounded **after** quota read (for success, process
   failure, timeout, cancellation, and invalid output alike).
5. Append a pseudonymized observation record to the JSONL store and attach a
   non-authoritative summary to the receipt — without changing the outcome.

The replay short-circuit performs no quota reads. Observation exceptions and log
failures are caught, redacted, and swallowed; receipt/ledger failures keep their
existing transport-v2 semantics and are never reclassified as observation
failures.

## Transport facts (codex app-server, experimental)

- The default transport is JSON-RPC 2.0 over stdio, newline-delimited JSON.
- Method `account/rateLimits/read` returns a single-bucket `rateLimits` and a
  multi-bucket `rateLimitsByLimitId` (keyed by `limit_id`). Each snapshot has
  `primary`/`secondary` windows with an integer `usedPercent`, `resetsAt`
  (unix seconds|null), and `windowDurationMins` (int|null).
- `windowDurationMins == 10080` is **never** treated as "weekly". The reader
  records every window of every candidate and only compares matching candidate
  identifiers across snapshots.
- A second call, `account/read`, supplies the account identity that is hashed
  (salted SHA-256) into the account pseudonym. If it is unavailable, the record
  omits the pseudonym and continues in degraded mode.

## Privacy

Only normalized observational fields are persisted. Raw `orgId`, `accountId`,
`email`, `plan`, tokens, headers, and endpoint credentials are never stored.
Account correlation uses a salted SHA-256 digest; limit/meter labels (which may
embed plan names) are pseudonymized into stable candidate ids. A 32-byte random
salt is generated beside the log (mode `0600`), reused for that store, and never
copied into receipts or the repository. If the salt cannot be created or read
safely, no account pseudonym is recorded and observation continues degraded.

## Latency budget

Each quota read has a small, configurable timeout independent of the review
timeout: **default 5 seconds, hard maximum 15 seconds** per snapshot. Eligibility
checks and report generation use the same budget. Observation may add bounded
latency; it cannot change review scheduling, outcome, exit status, verdict,
correction count, or ledger status.

## Storage, rotation, retention

- Default store: `<output-dir>/quota-observations.jsonl`, outside the reviewed
  worktree, created at mode `0600`.
- Writers are serialized with an `fcntl` file lock; each record is one atomic
  JSON-line append (no interleaving, no loss under concurrency).
- Rotation at **10 MiB**; rotated files retained **90 days**, always retaining at
  least the **five newest** rotated files. All three are operator-overridable.
- Diagnostics are bounded and redacted; raw RPC responses are never persisted.

## Review phases

The wrapper gains a required `--review-phase` selector with closed values
`plan_challenge`, `milestone`, `correction`, `final` (reviews) and `doctor`
(doctor). It is receipt/observation metadata only and is **never** sent as a new
verdict kind. Combinations are validated: `plan_challenge` requires
`review_kind=plan`; `milestone`/`correction`/`final` require `review_kind=milestone`;
`doctor` carries no verdict or review kind.

## Evaluation (after the observation window)

After each successful append, a bounded eligibility check runs. The window
closes once there are **at least 28 days and at least 30 observed intervals**,
spanning both `plan` and `milestone` review kinds. Before eligibility, no final
report is emitted (`continue_observation`). Once eligible, exactly one current
report is kept beside the log (`quota-report.json` + `quota-report.md`, mode
`0600`); re-running on an unchanged record set is a no-op (idempotent on a digest
of the processed records), and a changed record set replaces it atomically.

The report computes coverage by day/kind/phase/model/outcome, snapshot-status
counts (including corrupt records counted and skipped), every observed candidate
shape, reset/window/account mismatch and null-delta proportions, `usedPercent`
maxima and counts at ≥60% and ≥70%, and per-candidate delta correlation labelled
as correlation rather than attributed usage.

### Recommendation rule (versioned, deterministic)

- `continue_observation` while the minimum window/sample/coverage is unmet.
- `consider_advisory` once eligible when, for any one stable candidate identity,
  at least **3** distinct completed intervals reach `usedPercent >= 70`, **or** at
  least **20%** of intervals in which that candidate is valid reach
  `usedPercent >= 60`.
- `no_action_warranted` for every other eligible sample (including an absent or
  unusable signal).

Each interval is counted at most once per threshold per candidate. The report
states the exact rule it applied, with numerators, denominators, and supporting
interval ids. Changing the rule requires explicit operator configuration and
creates a new report-policy version; it never changes review runtime behavior.
Regardless of recommendation, the terminal status is `decision_required`.

## Operator configuration

| Knob | Default | Env / flag |
|---|---|---|
| Observation log path | `<output-dir>/quota-observations.jsonl` | `--quota-observations <path>` |
| Per-read timeout | 5s (max 15s) | `--quota-timeout <seconds>` |
| Rotation threshold | 10 MiB | overridable in `quota_observation.append_observation` |
| Retention | 90 days, ≥5 newest | overridable in `quota_observation.append_observation` |

## Files

- `scripts/read_codex_quota.py` — observation-mode reader (no gating).
- `scripts/quota_observation.py` — normalization, delta, JSONL logging, rotation.
- `scripts/codex_process.py` — shared process-group lifecycle (backports the
  orphan fix to the existing review/doctor timeout paths too).
- `scripts/summarize_quota_observations.py` — eligibility + deterministic report.
- `schemas/codex-quota-snapshot.schema.json` — observational record fields.
- `schemas/codex-quota-report.schema.json` — aggregated, non-authoritative report.
