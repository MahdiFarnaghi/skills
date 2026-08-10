# Codex review adapter (openai-codex/codex plugin, v1.0.6)

Isolates version-sensitive plugin transport from the core workflow. The skill's intended mode is **autonomous supervision**: Claude contacts Codex directly through a public model-callable review transport, converges on approval, and stops at the boundary. This reference defines the required capability contract and states truthfully whether the installed plugin provides it. The plugin command namespace is `codex` (commands appear as `/codex:...`).

## Required transport capability contract

Autonomous milestone supervision is supported only when ALL of the following are available in one public, model-callable interface:

1. **Public model invocation** — Claude may invoke a review without an operator.
2. **Cumulative or baseline-relative Git scope** — working tree, branch, or diff from a named baseline.
3. **Read-only reviewer** — Codex cannot modify production artifacts during review.
4. **Structured verdict and findings** — `verdict: approve|needs-attention` with severity, file/location, evidence, and recommendation.
5. **Wait/status/result lifecycle** — Claude can wait for and retrieve the tracked result.
6. **Stable job identity and target** — a review job whose `Target` records the reviewed scope, with provenance distinct from ordinary task jobs and stop-hook results.
7. **Origin marking and recursion prevention** — a review launched by Claude is marked as a Codex review from Claude with bounded delegation depth, and Codex performs it directly without delegating back to Claude.

If any capability is missing, autonomous supervision is unavailable; name exactly which capability is absent.

## Capability verdict for plugin v1.0.6 — autonomous supervision is NOT operational

Inspected surfaces (commands, agents, skills, MCP):

- `/codex:review` and `/codex:adversarial-review` are the ONLY structured-review transports: `--scope auto|working-tree|branch`, `--base <ref>`, `--wait`, structured `verdict: approve|needs-attention` with findings, `jobClass: review`, read-only. Both carry `disable-model-invocation: true` — Claude CANNOT invoke them. `/codex:transfer` is likewise operator-only. (Capabilities 2–6 are present but operator-gated; capability 1 is absent.)
- `/codex:rescue` and the `codex-rescue` agent ARE model-callable, but they forward to a general Codex `task` that defaults to `--write` (write-capable), returns free-form text, records an ordinary task job (`jobClass: task`, `kindLabel: rescue`), and are explicitly barred from calling `review`. This is a task-delegation surface, not a review transport. Using it as a review would violate read-only operation (3), structured verdict (4), and review provenance (6), and would simulate a result.
- No MCP server or MCP tool is declared by the plugin. `codex-cli-runtime` is `user-invocable: false`, internal to the rescue agent.
- The plugin's `codex-result-handling` guidance forbids auto-applying review fixes and requires asking the user before any change, which conflicts with the autonomous fix-and-rereview loop.
- No origin/delegation metadata exists (capability 7 absent).

Missing for autonomous supervision: capability **1** (public model-callable review invocation), capability **7** (origin marking + recursion prevention), and a result-handling rule that permits Claude-initiated correction cycles.

## Primary mode — autonomous model-callable review (intended; not yet provided)

When a future plugin version supplies the capability contract, Claude operates autonomously:

- invoke the public model-callable review transport directly with the milestone scope and the compact review focus (the validated packet);
- wait for the tracked review result (`--wait` or status/result retrieval);
- validate the result is a completed review-class job whose `Target` matches the intended scope, concerns the milestone and actual affected code, and carries a structured verdict;
- on `needs-attention`, classify findings, fix accepted ones, rerun verification, and re-invoke until `verdict: approve`;
- stop at the milestone boundary only after approval.

Read-only enforcement and recursion protection must come from the transport, not from skill prose. Never call private plugin scripts (e.g. `codex-companion.mjs`) directly, never simulate a result, never manufacture fake Git changes or commits, and never use the write-capable rescue/task surface as a review.

## Fallback mode — operator-mediated review

Until the primary mode exists, review is operator-mediated and is a FALLBACK, not the intended workflow:

- `/codex:adversarial-review --wait --scope working-tree <focus>` (or `/codex:review`, which takes no focus; `--base <ref>` or `--scope branch` are alternatives). Match the packet's Diff mode to the scope (`cumulative working tree` → `--scope working-tree`; `isolated since baseline` → `--base <ref>`).
- `disable-model-invocation: true` means the operator must run it; Claude prepares the packet, gives one exact command, and pauses. Each correction re-review needs another explicit operator command.
- This fallback must be explicitly accepted by the user. It does NOT satisfy a request for unattended milestone supervision, and the skill must not present it as such.

## Turn-scoped stop review (Stop hook)

Separate and distinct from cumulative review:

- Fires on every `Stop` when `config.stopReviewGate` is true; reviews ONLY the immediately preceding Claude turn (`last_assistant_message`); reads no packet, Git scope, baseline, or earlier turn.
- Emits a free-text first line `ALLOW: <reason>` or `BLOCK: <reason>`; `jobClass: task`, `kindLabel: rescue`, title `Codex Stop Gate Review`.
- Returns `ALLOW` by design for non-code turns (status/setup/reporting/checks/docs). A prose packet does not expand its scope.
- A stop-gate `ALLOW` is NEVER milestone approval. Repeated stop turns cannot manufacture cumulative approval.

The repository's `quality_check_reminder.sh` Stop hook (when present) is unrelated and non-blocking; it never records a review verdict.

## Recursion protection

A Codex review launched by Claude must be marked as a Codex review originating from Claude (e.g. `hostOrigin: claude-code`, `jobPurpose: codex-review`, bounded delegation depth, or the transport's supported equivalent) and Codex must perform it directly — it must not delegate the review back to Claude through a reverse-direction review or the rescue path. Plugin v1.0.6 provides no such marking; this is part of why autonomous supervision is unavailable.

## Decision procedure

1. Run the capability check above against the installed plugin.
2. If all seven capabilities are present → primary (autonomous) mode.
3. If any is absent → autonomous supervision is unavailable. If the user's workflow requires it, STOP and report the exact missing capability and the required plugin enhancement. Offer operator-mediated review only as an explicitly accepted fallback; never silently downgrade to Claude-only execution.
