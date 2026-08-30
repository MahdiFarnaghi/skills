# Agent Skills

A personal collection of reusable skills for Claude Code and Codex. Each skill
is a self-contained directory with a `SKILL.md` entrypoint and optional
references, scripts, schemas, and UI metadata.

## Skills

### `anki-creator`

Creates Anki-ready, semicolon-delimited CSV flashcards from pasted vocabulary
or a text file. It detects the source language and can generate definitions,
translations, examples, synonyms, antonyms, word types, and grammatical
information.

Use it for language-learning vocabulary and flashcard generation. Ask for
example: “Create Dutch Anki cards from `/path/to/words.txt`.”

### `codex-reviewed-implementation`

Runs a bounded implementation workflow for substantial or high-risk software
changes. Claude Code owns implementation, while Codex performs independent,
read-only milestone reviews through the skill’s review wrapper. It includes
execution-profile binding, evidence ledgers, safety-critical workflows,
Docker-first verification, correction loops, and final integration review.

Use it when implementation risk justifies an independent Codex review. It is
not intended for routine small fixes unless explicitly requested.

### `codex-orchestrated-implementation`

Coordinates a reciprocal two-worker implementation pair under a confirmed Terra
orchestrator. One worker implements a milestone while the other independently
reviews it; they swap roles on the next milestone. Claude Code and Luna are
supported workers. The skill includes strict project configuration, immutable
Git handoffs, scoped fingerprints, worker receipts, append-only ledgers,
correction limits, and safety-critical verification.

Use it when work benefits from delegated implementation and reciprocal review.
The target must be a Git repository with a project-level
`.codex-orchestration.toml`; copy `config/example.toml` to create one. The v2
policy uses Terra as technical authority, Luna as orchestrator, and alternating
Luna/Claude workers. It also supports an optional constrained Qwen3.8-27B local
assistant for bounded read-only tasks; Qwen is never an authority, reviewer, or
completion agent. If the local server requires authentication, set
`auth_mode = "explicit_env"` and `auth_token_env = "LLM_BEARER_TOKEN"`; export
that environment variable at runtime. Never put the bearer token in the project
configuration.

The orchestration skill also supports Terra-approved Claude failover. A Claude
quota or transport failure is recorded without a verdict; Luna can continue
only with a distinct worker session and independent review context.

## Installation

### Codex

Copy the skill directories you want into your Codex skills directory:

```sh
mkdir -p ~/.codex/skills
cp -R skills/anki-creator ~/.codex/skills/
cp -R skills/codex-reviewed-implementation ~/.codex/skills/
cp -R skills/codex-orchestrated-implementation ~/.codex/skills/
```

Restart or refresh Codex so it reloads the skill catalog. Invoke a skill by
name, for example `$codex-orchestrated-implementation`.

### Claude Code

Copy the skill directories into Claude Code’s skills directory:

```sh
mkdir -p ~/.claude/skills
cp -R skills/anki-creator ~/.claude/skills/
cp -R skills/codex-reviewed-implementation ~/.claude/skills/
cp -R skills/codex-orchestrated-implementation ~/.claude/skills/
```

The orchestration skill expects the supported Claude Code companion capabilities
for implementation and review. In particular, it uses `$cc:rescue` for Claude
implementation and `$cc:review` only for read-only review when the environment
is already bound to the frozen worktree.

### skills.sh

Once this collection is published in a public GitHub repository, install its
skills through the [skills.sh](https://www.skills.sh/) CLI:

```sh
npx skills add <owner>/<repo>
```

Replace `<owner>/<repo>` with the GitHub repository containing this collection.
The CLI makes the repository’s skills available to the supported agent tools;
see the [skills.sh documentation](https://www.skills.sh/docs) for current CLI
behavior. Review a skill’s files before installing, especially skills that can
edit code or run commands.

## Project setup

For `codex-orchestrated-implementation`, create the project policy from the
provided example and validate it:

```sh
cp ~/.codex/skills/codex-orchestrated-implementation/config/example.toml \
   .codex-orchestration.toml
printf '%s\n' '.codex-orchestration.toml' >> .gitignore
python3 ~/.codex/skills/codex-orchestrated-implementation/scripts/validate_config.py \
   --config .codex-orchestration.toml
```

Review the worker pair, model choices, `first_implementer`, and workflow profile
with the developer before using the skill on a project. Keep the generated
project policy local and untracked; the skill’s `config/example.toml` remains
the shareable template.

For `codex-reviewed-implementation`, follow the model-policy and Codex CLI
preflight/doctor instructions in its references before starting a reviewed
milestone.

## Validation

Validate a skill package with the Codex skill validator:

```sh
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
   ~/.codex/skills/codex-orchestrated-implementation
```

Run the orchestration skill’s focused tests with Python 3.11:

```sh
python3.11 -m pytest \
  ~/.codex/skills/codex-orchestrated-implementation/scripts/test_validate_config.py \
  ~/.codex/skills/codex-orchestrated-implementation/scripts/test_validate_handoff.py \
  ~/.codex/skills/codex-orchestrated-implementation/scripts/test_end_to_end.py
```

The control-plane and Qwen contract tests are included in the same package and
can be run with:

```sh
python3.11 -m pytest ~/.codex/skills/codex-orchestrated-implementation/scripts/test_control_plane.py
```

## Contributing

Keep each skill focused and self-contained. Update its `SKILL.md`, references,
schemas, tests, and this README together when its behavior or installation
requirements change.
