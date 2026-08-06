# Codex review adapter (openai/codex-plugin-cc)

Isolates version-sensitive plugin transport from the core workflow. The core skill stays transport-neutral: request an independent, read-only Codex review through the configured adapter. This reference states what the adapter assumes about the installed plugin and how to fail safely when those assumptions break. The skill is still specifically designed for Codex review; the goal is to isolate plugin-version assumptions, not to support arbitrary reviewers.

## Adapter responsibilities

Before relying on the plugin, the adapter must:

- verify the installed `openai/codex-plugin-cc` behavior against the assumptions below;
- use documented public commands only;
- fail visibly when installed behavior differs from these assumptions;
- never silently claim a review occurred;
- never create fake tracked changes or commits to manufacture a review target;
- preserve the compact-packet and no-manual-report-relay goals of the core workflow.

## Adapter assumptions and verification

Confirm each before relying on the gate:

1. The plugin is installed and `/codex:setup` succeeds.
2. The review gate is enabled so a review runs without manual report relay.
3. The review commands target Git changes and invoking them disables model invocation; the review must run as the operator or agent boundary, not as a self-invoked model call.

If any of these differ from the assumptions, stop, report the exact mismatch, and follow the operator fallback below.

## Enabling and disabling the review gate

- To enable hands-off reviews, ask the user once to run `/codex:setup --enable-review-gate`.
- At final completion, remind the user that the optional gate can be disabled with `/codex:setup --disable-review-gate`.

## Requesting a review

Use the enabled review gate so the targeted Codex review runs automatically at the milestone or plan boundary. Because the review commands disable model invocation, never pretend to invoke `/codex:review` or `/codex:adversarial-review` autonomously and never bypass that restriction by calling private plugin scripts.

Keep handoffs compact: emit the validated review packet as routing information and let the gate attach the repository diff. Do not manually relay Codex reports or paste transcripts back into the conversation.

## Empty or unusable review target

The plugin reviews Git changes. If there is no reviewable plan or milestone artifact, or the gate reports an empty target:

1. do not create fake tracked changes or commit temporary review material;
2. do not call private plugin scripts or claim a review occurred;
3. present the validated packet and ask the user for one explicit review invocation, or ask permission to proceed without that review;
4. resume from the revised baseline without manually relaying long reports.

When the project requires a durable plan or specification as part of the task, update that real artifact before the review and review it normally.

## Continuation behavior

In the installed transport, a successful review gate ends the current Claude turn and model invocation of the review commands is disabled, so proceeding to the next milestone needs a one-word `continue` from the user. This `continue` is genuinely required by this transport, not a general workflow rule; in a Lightweight single-milestone task it is not required at completion. Never replace it with manual copying of Codex reports or large corrective prompts.

## Operator fallback

If the plugin or review gate is unavailable, or installed behavior contradicts the assumptions above, pause before implementation and offer either ordinary Claude-only execution or an operator-invoked `/codex:adversarial-review`. Report the exact reason the autonomous review could not run. Never silently claim a review occurred and never weaken the requested workflow without surfacing the fallback.
