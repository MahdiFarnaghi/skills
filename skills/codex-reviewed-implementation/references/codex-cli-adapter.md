# Codex review adapter (Codex CLI, codex-cli 0.146.0)

Isolates version-sensitive CLI transport from the core workflow. The skill's
intended mode is **autonomous supervision**: Claude contacts Codex directly
through the public Codex CLI, converges on approval, and stops at the boundary.
This reference defines the required capability contract, records whether the
installed CLI provides it, and pins the exact verified invocation.

Historical delivery notes are kept in `decisions.md`; this reference describes
the current adapter. The Claude-side Codex **plugin**
(`/codex:review`, `/codex:adversarial-review`) is operator-only
(`disable-model-invocation: true`) and is retained here only as a labeled
fallback. Plain structured Codex **CLI** execution (`codex exec`) is the public
model-callable transport this skill automates.

## Required transport capability contract

Autonomous milestone supervision is supported only when ALL of the following are
available in one public, model-callable interface:

1. **Public model invocation** — Claude may invoke a review without an operator.
2. **Cumulative or baseline-relative Git scope** — working tree, branch, or diff from a named baseline or commit.
3. **Read-only reviewer** — Codex cannot modify production artifacts during review.
4. **Structured verdict and findings** — `verdict: approve|needs-attention` with severity, file/location, evidence, and recommendation.
5. **Synchronous, bound result** — Claude receives the result of the exact invocation in-process (foreground/blocking), correlated to one target.
6. **Stable target identity** — the reviewed worktree, branch, baseline, scope, and content fingerprint are recorded and re-checked.
7. **Origin marking and recursion prevention** — the review is performed directly by Codex and must not load Claude-facing skill/orchestration instructions or delegate back to Claude.

If any capability is missing, autonomous supervision is unavailable; name exactly
which capability is absent.

## Capability verdict for codex-cli 0.146.0 — autonomous supervision IS operational via the CLI

Verified directly against the installed CLI (probe-confirmed, not assumed):

- **Public model invocation (1): YES.** Plain `codex exec` is public and model-callable.
- **Git scope (2): YES, wrapper-enforced.** Codex CLI deliberately conflicts
  native selectors (`--uncommitted`, `--base`, `--commit`) with the custom prompt
  required for the review packet. The wrapper therefore uses plain structured
  exec, resolves refs to immutable SHAs, supplies exact Git
  inspection commands, fingerprints the target, and rejects mismatched output.
- **Read-only reviewer (3): YES.** Read-only is forced at the `exec` level with
  `-s read-only`. (The `review` subcommand rejects `-s`; see the invocation
  below.) A runtime mutation test must still prove it for Safety-critical use.
- **Structured verdict (4): YES.** `--output-schema <FILE>` forces the model's
  final response into the shape of `schemas/codex-review-output.schema.json`;
  `-o <FILE>` writes that verdict to a file outside the worktree. The wrapper
  re-validates it locally.
- **Synchronous, bound result (5): YES.** The wrapper runs foreground/blocking,
  captures the real exit status, timeout, and termination reason, and writes a
  target-bound receipt.
- **Stable target identity (6): YES.** The wrapper records repository, worktree,
  git-common-dir, branch, HEAD, baseline, staged and unstaged binary diffs,
  untracked contents, and packet digest, and rejects a verdict whose `target`
  does not match or whose content fingerprint changes during review.
- **Recursion prevention (7): PARTIAL, layered.** The wrapper prompt forbids
  loading Claude-facing skill/companion/orchestration instructions and
  `--ignore-rules` keeps project `.rules` from overriding the boundary. The CLI
  is a separate process (not a nested agent call), which lowers recursion risk
  versus the plugin path, but there is no cryptographic origin marker.

All seven capabilities are satisfied for Standard and Lightweight review.
Capability 7 is layered (prompt + flag), not cryptographic; for Safety-critical
use the runtime read-only and instruction-boundary tests documented by the
skill must
prove the boundary holds.

## Verified invocation (the only form the wrapper uses)

The `review` operation does not reliably carry `--output-schema`, and every
native selector conflicts with `[PROMPT]`. The automated path therefore uses
plain exec with an explicit reviewer contract; stdin is closed from `/dev/null`:

```text
codex exec -C <abs-worktree> -s read-only \
  --output-schema schemas/codex-review-output.schema.json \
  -o <verdict-out.json> --ephemeral --ignore-rules [-m <model>] \
  <reviewer-contract-scope-and-packet>     # stdin closed from /dev/null
```

The wrapper `scripts/run_codex_review.py` builds this as an argument vector
(never a shell string), enforces scope exclusivity, resolves refs via Git
argument separation to immutable object ids, runs the process in its own session with a bounded timeout,
captures stdout/stderr separately with secret redaction, validates the verdict
locally, re-checks the content fingerprint after Codex exits, and writes a
receipt plus a locked, idempotent loop-state ledger.

## Wrapper interface

```
python3 scripts/run_codex_review.py preflight [--worktree <root>] [--schema <schema>]
python3 scripts/run_codex_review.py doctor --worktree <root> --schema <schema> --receipt <outside-worktree.json> [--model <model>]
python3 scripts/run_codex_review.py review \
  --review-kind plan|milestone \
  --worktree <abs-root> --scope uncommitted|base|commit [--base <ref>|--commit <sha>] \
  --milestone <id> --round <n> --packet <packet> --schema <schema> \
  --output-dir <dir-outside-worktree> [--timeout <sec>] [--model <m>] \
  [--fingerprint-scope worktree|packet] \
  --doctor-receipt <matching-doctor.json> [--state-ledger <path>]
```

Preflight spends no Codex usage: it checks the binary, version, auth, schema,
and the `-s`-at-exec-level invariant via a zero-cost arg-parse probe. Review
returns a closed failure enum with a fixed remediation string per value; the
authoritative mapping is `OUTCOMES` in `scripts/run_codex_review.py`. Exit 0
means a valid verdict was obtained; read the receipt for `approve` versus
`needs-attention`. Every transport failure exits
non-zero and produces no verdict.

The verdict schema is version 3. Existing doctor receipts are invalidated by
the schema digest and must be regenerated. Existing review outputs without the
required finding `origin` field are rejected; start a new review round after
upgrading.

## Fallback mode — operator-mediated plugin review (NOT the automated path)

When preflight or the matching live doctor fails (missing CLI, failed auth,
unsupported version, changed transport shape, schema failure, or read-only
failure), review may fall back to the operator-only plugin commands.
This is a FALLBACK, not the intended workflow, and must be explicitly accepted by
the user:

- `/codex:adversarial-review --wait --scope working-tree <focus>` (or `/codex:review`,
  which takes no focus; `--base <ref>` or `--scope branch` are alternatives). Match
  the packet's Diff mode to the scope.
- `disable-model-invocation: true` means the operator must run it; Claude prepares
  the packet, gives one exact command, and pauses. Each correction re-review needs
  another explicit operator command.

This fallback does NOT satisfy a request for unattended milestone supervision,
and the skill must not present it as such.

## Turn-scoped stop review (Stop hook) — separate and distinct

- Fires on every `Stop` when `config.stopReviewGate` is true; reviews ONLY the
  immediately preceding Claude turn; reads no packet, Git scope, baseline, or
  earlier turn.
- Emits a free-text first line `ALLOW: <reason>` or `BLOCK: <reason>`; it is a
  task-class result, not a milestone verdict.
- A stop-gate `ALLOW` is NEVER milestone approval. Repeated stop turns cannot
  manufacture cumulative approval.

The repository's `quality_check_reminder.sh` Stop hook (when present) is
unrelated and non-blocking; it never records a review verdict.

## Recursion protection

A Codex review launched by Claude is bounded by the wrapper's instruction prefix
and by `--ignore-rules`: it must review repository evidence directly and must not
load Claude-facing skill, companion-plugin, or orchestration instructions, and
must not delegate back to Claude. There is no cryptographic origin marker in the
CLI; the boundary is enforced by prompt plus flag and proved by the
instruction-boundary forward test. Finding provenance is schema-bound metadata
from the review call, not a cryptographic process-origin claim.

## Decision procedure

1. Run `scripts/run_codex_review.py preflight`.
2. Verify a matching successful doctor receipt; run `doctor` once after any CLI, platform, transport, or schema change.
3. If both pass → autonomous mode. Run review through the wrapper.
4. If either fails → autonomous supervision is unavailable for this
   environment. Report the exact failure. Offer operator-mediated plugin review
   only as an explicitly accepted fallback; never silently downgrade to
   Claude-only execution, and never claim automation when the transport is
   unproven.

Re-run preflight whenever the Codex CLI is updated, and whenever the supported
flag placement changes (preflight's `-s`-at-exec-level probe detects that).
