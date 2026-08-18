# Codex model selection and execution profiles (Phase 7)

Phase 7 makes the review model explicit, recorded, and bound. The core
object is the **execution profile**: `{model, reasoning_effort, codex_profile,
config_version, resolution_source, native_model_pin, native_effort_pin,
profile_digest}`. It is resolved exactly once per invocation, frozen, and
stamped into doctor receipts, review receipts, loop-state ledger rounds, and
quota observations. `profile_digest` hashes only execution-affecting fields
(model, effort, codex profile, config version, and the effective native
model/effort pins — see "Native-binding boundary" below) — provenance
(`resolution_source`) is recorded but never changes authorization.

Before Phase 7, `--model` defaulted to `None`, so reviews silently ran
whatever `~/.codex/config.toml` pinned and receipts recorded `model: null`,
leaving no local trace of the effective model. Phase 7 exists to close that
gap.

## Resolution precedence

```
1. CLI flags     --model <id|cli-default> | --reasoning-effort <e> | --codex-profile <p>
2. Project config  <worktree>/.codex-review.toml   (malformed = loud error, never a silent default)
3. Recorded native default                        (no policy anywhere)
     - resolution_source = user_config  when ~/.codex/config.toml pins a model
     - resolution_source = cli_default  otherwise
     - a warning is emitted; zero-config projects keep working
4. --require-config (CI) turns case 3 into a fast E_CONFIG_MISSING failure
```

`skill-wide config/defaults.toml` deliberately ships with **no model pin**:
it documents the convention only, so a skill update can never silently
change (or invalidate receipts for) any project's model policy.

The `cli-default` sentinel (`--model cli-default` or `model = "cli-default"`)
restores pre-Phase-7 behavior — no `-m` is passed and the CLI chooses. It
normalizes to `model: null` in the profile and never rots.

## Project config (closed schema)

```toml
version = 1
model = "gpt-5.6-luna"          # exact id recommended; or "cli-default"
reasoning_effort = "medium"      # low | medium | high | xhigh | max
codex_profile = "review"         # optional native -p pass-through
```

- Only these four keys are accepted; unknown keys are rejected on load
  (catches `reasoning-effort` vs `reasoning_effort` typos), and
  `init-config` validates values at the write seam so it can never emit a
  config the loader rejects.
- No catalog metadata, timestamps, or `[escalation]` section: the
  version-controlled file stays declarative and diff-stable. Provenance
  lives in the advisory cache; escalation has no defined consumer and was
  cut (reintroduce only with a tested deterministic trigger).
- Exact ids bind receipts; possible aliases or retired ids are flagged as a
  **warning** by `validate-config` when the advisory catalog can resolve
  them (advisory, never blocking) — run `list-models` to pick an exact slug.

## Commands

```bash
# Non-interactive (agent) bootstrap — idempotent, never blocks on a prompt:
python3 scripts/run_codex_review.py init-config \
  --worktree "$ROOT" --model gpt-5.6-terra --reasoning-effort medium

# Human interactive picker (TTY only), worktree defaults to cwd:
python3 scripts/run_codex_review.py init-config

# Print the resolved profile + winning precedence level; never rewrites:
python3 scripts/run_codex_review.py validate-config --worktree "$ROOT"

# Advisory listing (compatibility from the CLI's own cache; API prices
# labeled as not reflecting subscription quota):
python3 scripts/run_codex_review.py list-models --json [--refresh]
```

## Error codes and verbatim templates

| Condition | Code | Message template |
|---|---|---|
| No model pinned + `--require-config` set (CI) | `E_CONFIG_MISSING` | `E_CONFIG_MISSING: No review model is configured for <worktree> and --require-config is set (CI mode).` Fix: `init-config --worktree <path> --model <MODEL>` or pass `--model <MODEL>` (or `--model cli-default`). Note: an effort-only flag does NOT satisfy it — the model dimension must be pinned. |
| Malformed/unknown-key/bad-version/bad-effort config | `E_CONFIG_MALFORMED` | `E_CONFIG_MALFORMED: config <path> ... <exact problem + offending key>.` Fix: correct the key, then `validate-config --worktree <path>`. |
| `init-config` over existing file | `E_CONFIG_EXISTS` | `E_CONFIG_EXISTS: config <path> already exists; pass --force to replace it (existing project policies are never silently overwritten).` |
| Doctor receipt bound to a different profile (including pre-Phase-7 receipts) | outcome `doctor_required` | `doctor receipt mismatch: profile_digest (profile changed or this is a pre-Phase-7 receipt; re-run doctor with the current profile to certify it)` |
| Completed ledger round re-entered under a different profile | outcome `profile_changed` (exit 78) | `round <key> completed under a different execution profile; ... Re-run with --round <N+1> under the new profile.` |

## Migration notes

- Upgrading invalidates existing doctor receipts once (they lack the profile
  keys): the first `review` returns `doctor_required` with the re-doctor
  remediation. That is deliberate fail-closed behavior.
- Pre-Phase-7 ledger entries lack `profile_digest`; re-entering those rounds
  raises the standard round-conflict error naming `profile_digest` — start a
  new round. (A recorded digest that differs on a *completed* round is the
  `profile_changed` advance case; a *running* round always keeps mutual
  exclusion regardless of profile mismatch.)

## Advisory catalog invariants

- `~/.codex/models_cache.json` (the CLI's own cache) is the compatibility
  source — read fail-soft; its schema is undocumented and churns.
- The public OpenAI models page is pricing annotation only (`--refresh`),
  parsed as untrusted data, and labeled: API prices do not model
  subscription quota consumption.
- The catalog lives in `codex_model_catalog.py`, which the review execution
  path never imports (test-enforced). Catalog state can degrade `list-models`
  display only; **the paid doctor is the sole validator of a profile**.
- Cache files live under `$XDG_CACHE_HOME/codex-reviewed-implementation/`
  (or the platform cache home) — outside every reviewed worktree by
  construction; `ensure_cache_path_outside_worktree` guards custom cache
  paths for API callers (realpath-aware, symlink-safe). Cache writes are
  best-effort: an unwritable cache directory is ignored, never fatal.
- `list-models --refresh` output labels its origin honestly: `native`
  (CLI cache), `web` (page-parsed, no CLI compatibility info), `cached`,
  or `none`.

## Native-binding boundary (what the digest does and does not bind)

The profile digest binds the wrapper-controlled policy (model, effort,
codex profile name, config version) plus the two named native settings that
silently govern execution when nothing overrides them: the user
`config.toml` **model pin** and **model_reasoning_effort pin** (read
fail-soft; bound only when effective, including under `cli-default`).

It deliberately does **not** hash the full `config.toml` or the contents of
the file a `-p` profile selects: doing so would invalidate every doctor
receipt on any unrelated user-config edit. Residual risk, accepted and
documented: a user editing *other* execution-affecting settings inside a
selected profile file can change what runs without changing the digest.
Tightening this (e.g., hashing the resolved profile file) is an open design
question in TODOS.md.
