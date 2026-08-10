# Codex review adapter (openai-codex/codex plugin, v1.0.6)

Isolates version-sensitive plugin transport from the core workflow. The core skill stays transport-neutral: request an independent, read-only Codex milestone review, then verify the resulting verdict. This reference states exactly what the installed plugin provides, the two distinct review transports, and how to fail safely when behavior differs. The plugin command namespace is `codex` (commands appear as `/codex:...`).

## Two distinct review transports

The plugin provides two review transports with different scopes, outputs, and invocation models. Do not confuse them.

### Turn-scoped stop review (the Stop hook)

- Fires automatically on every Claude `Stop` event when `config.stopReviewGate` is true (set by `/codex:setup --enable-review-gate`).
- Reviews ONLY the immediately preceding Claude turn. Its sole input is that turn's assistant message. It reads no review packet, no Git scope, no baseline, and no earlier turn.
- Output: a single first line `ALLOW: <reason>` or `BLOCK: <reason>` (free text). Job records show `jobClass: task`, `kindLabel: rescue`, `title: "Codex Stop Gate Review"`.
- By design it returns `ALLOW` without investigation whenever the previous turn made no direct code changes (status, setup, reporting, checks-only, or documentation-only turns). These `ALLOW`s are not verdicts for any milestone.
- A prose packet in the turn does NOT expand its scope, and it attaches no packet-directed diff.
- Use: optional immediate feedback on the edits made in one specific turn. It is NOT the milestone acceptance gate. Never treat a stop-gate `ALLOW` as cumulative milestone acceptance.

### Explicit milestone review (operator-invoked commands)

- Public commands `/codex:adversarial-review` and `/codex:review` review a real Git scope: `--scope working-tree|branch|auto`, optionally `--base <ref>`. `/codex:adversarial-review` also accepts free-text focus routing after the flags; `/codex:review` does not.
- `disable-model-invocation: true`: Claude CANNOT invoke them. The operator must. Claude prepares the packet and pauses with one exact command; it never pretends to run the command and never calls private plugin scripts.
- Use `--wait` so the job completes and writes its result before control returns.
- Output is structured JSON `{"verdict":"approve"|"needs-attention","summary":...,"findings":[...],"next_steps":[...]}`, rendered as Markdown with a `Target:` line (the reviewed Git scope), a `Verdict:` line, findings with `file:line`, severity, and recommendation, and `Next steps`. Job records show `jobClass: review`, `kind: adversarial-review` (or `review`).
- This is the ONLY transport that performs a cumulative milestone review of the working tree, branch, or a base diff.

Note the output contracts differ: the stop gate emits free-text `ALLOW:`/`BLOCK:`; the operator commands emit structured `verdict: approve|needs-attention`. A milestone is accepted on a scoped `verdict: approve`, never on a stop-gate `ALLOW:`.

## Request a milestone review

At every cumulative milestone, plan-challenge, or final-integration boundary:

1. Finish implementation and verification, reconcile the evidence ledger and docs, and prepare and validate the compact packet.
2. Give the operator ONE exact documented command with concise focus text distilled from the packet (name the milestone, the reviewed scope, and the key concerns), for example:
   `/codex:adversarial-review --wait --scope working-tree <focus text>`
   Match the command scope to the packet's Diff mode: `cumulative working tree` → `--scope working-tree`; `isolated since baseline` → `--base <ref>` (or `--scope branch`). `/codex:review` is the non-adversarial variant and takes no focus text.
3. Pause without claiming acceptance. Do not instruct the operator to copy reports between systems; read the result from the plugin job.

The packet routes the reviewer's attention; it is not proof. The reviewer inspects the repository diff and tests independently.

## Validate a milestone verdict

After the operator runs the command, inspect the resulting job (the operator can show it via `/codex:result <job-id>` or `/codex:status <job-id>`). A valid milestone verdict requires ALL of:

- the job completed successfully (`status: completed`, `phase: done`) and is a review-class job (`jobClass: review`), not a stop-gate task;
- its output carries a structured `verdict` — `approve` to accept, `needs-attention` (with findings) to block — and a `Target:` line naming the intended Git scope;
- it substantively identifies or addresses the named milestone (it names the milestone and the actual code paths reviewed), not merely the previous turn;
- it is not a turn-scoped stop-gate `ALLOW` saying the previous turn made no code changes / was status-only / checks-only / documentation-only.

If `verdict: needs-attention` (or any finding), classify each finding (`accept`/`disprove`/`defer`/`escalate`), correct accepted findings, rerun the affected evidence, then request another explicit scoped milestone review. Only a scoped `verdict: approve` closes the milestone.

If the job fails, times out, exits status 1, produces no output, or yields only a turn-level stop-gate verdict, treat it as NO milestone verdict: record that no verdict occurred and follow the operator fallback. Never retry unchanged stop/status turns to obtain acceptance.

## Re-review after corrections

Corrections and the final integration review each require their own explicit scoped milestone-review invocation. Re-emitting a packet, making a no-op or artificial edit, or repeatedly ending turns does NOT turn the stop gate into a cumulative review and cannot produce a milestone verdict.

## Unattended cumulative review is not supported (plugin v1.0.6)

Fully unattended cumulative milestone review is NOT available through the stop gate: the stop gate reviews only the preceding turn, and the cumulative-scope review commands disable model invocation. Skill prose cannot add this capability. Do not claim hands-off milestone review is enabled merely because `stopReviewGate` is true. Enabling the stop gate remains optional for turn-level feedback only.

If the user requires completely unattended cumulative milestone reviews with no operator command, the workflow is blocked by the current plugin capability. Surface this plainly and explain that a plugin enhancement (a persisted review request plus Git scope for stop-time execution) is required. Do not fake unattended behavior with private scripts, fabricated review targets, false review claims, or repeated stop turns.

## Setup and assumption checks

Confirm each before relying on the transport:

1. The plugin is installed and `/codex:setup` succeeds.
2. The stop gate is optional and turn-scoped; enabling it (`/codex:setup --enable-review-gate`) does NOT enable cumulative milestone review. It may remain enabled for turn-level feedback.
3. The cumulative milestone review commands exist and disable model invocation, so milestone review is operator-invoked, not self-invoked.
4. The repository's `quality_check_reminder.sh` Stop hook (when present) is unrelated and non-blocking: it only nudges running repository checks and exits 0; it never records a review verdict. Never mistake it for a review block or verdict.

If installed behavior contradicts the above, stop, report the exact mismatch, and follow the operator fallback.

## Operator fallback

If the plugin or the milestone review command is unavailable, or installed behavior contradicts the assumptions above, pause before implementation and offer either ordinary Claude-only execution (with the milestone review explicitly waived by the user) or an operator-invoked scoped review. Report the exact reason a milestone verdict could not be obtained. Never silently claim a milestone review occurred and never weaken the requested workflow without surfacing the fallback.
