# TODOS

Deferred items from the Phase 7 /autoplan review (2026-08-18). Each entry has
enough context to pick up in three months.

## Model-migration surfacing in validate-config

- **What:** `validate-config --suggest-upgrade` reads `[notice.model_migrations]`
  from `~/.codex/config.toml` plus fresh `models_cache.json` data and suggests
  the replacement when a pinned model is retired.
- **Why:** Exact pins rot when OpenAI migrates models; today the only signal is
  a doctor failure with no remediation pointer.
- **Pros:** Retirement becomes a one-command fix instead of an investigation.
- **Cons:** Depends on two undocumented native surfaces; needs fail-soft reads.
- **Context:** Eng F7 / DX F8 from the Phase 7 review; `codex_model_config.py`
  is the natural home. Baseline: the reframed Phase 7 already warns on retired
  models via the catalog; this adds the *replacement suggestion*.
- **Effort:** S (human ~2h / CC ~20min) · **Priority:** P3
- **Depends on:** Phase 7 implementation (T1 canonical profile).

## Intent-tier policy abstraction

- **What:** Map policy names (`fast`, `standard`, `high-risk`) to profiles
  instead of exposing raw model IDs in project config.
- **Why:** Repos care about review strength/latency, not vendor model IDs that
  age; tiers survive model churn centrally.
- **Pros:** Config stops rotting; skill-wide tier updates propagate without
  touching every project.
- **Cons:** An abstraction layer over an abstraction layer; only worth it once
  more than ~2 profiles exist.
- **Context:** Codex CEO F6 / DX F5 from the Phase 7 review. Revisit after the
  thin-profile layer ships and real usage shows whether projects actually
  change models.
- **Effort:** M (human ~1d / CC ~1h) · **Priority:** P3
- **Depends on:** Phase 7 shipped + observed usage.

## Quota reader / reviewer full-profile parity

- **What:** Pass the frozen execution profile (effort, `-p` profile) into
  `QuotaObserver`/`read_codex_quota.py` so the quota-read `codex app-server`
  invocation is constructed from the same option surface as the review it
  observes (currently only the model is pinned).
- **Why:** A `codex_profile` that changes CLI configuration can make quota
  snapshots read under a different config than the review that attributed
  them; plan.md's parity claim is only partially true as-built.
- **Pros:** Observations attributable to exactly what ran; closes the plan
  delta.
- **Cons:** Requires probing which flags `codex app-server` accepts for
  effort/profile before wiring (undocumented surface).
- **Context:** Surfaced by the 2026-08-18 consistency review (red-team +
  Codex structured findings). `read_codex_quota.py:201` builds its own
  `-c model="..."`; `_exec_option_flags` in run_codex_review.py is the
  shared builder to reuse. Also decide digest semantics for a
  `--codex-config <k=v>` pass-through while here.
- **Effort:** M (human ~1d / CC ~1h) · **Priority:** P2
- **Depends on:** Phase 7 merge.

## Self-review weakening via worktree model config (open design question)

- **What:** Decide whether `.codex-review.toml` inside the reviewed worktree
  may set the profile for the reviews of that worktree (the review target
  currently picks its own reviewer model/effort), or whether the profile must
  come from operator-controlled sources (CLI flags / config outside the tree /
  config snapshot recorded at plan acceptance).
- **Why:** A malicious implementation diff can commit a weak-model config
  before the doctor run; the re-doctor + round-advance cost is a speed bump,
  not a barrier.
- **Pros of restricting:** removes the self-weakening vector entirely.
- **Cons:** loses per-repo convenience policy; adds operator burden.
- **Context:** Red-team + adversarial finding from the 2026-08-18 review
  (both marked INVESTIGATE — a human design call, not a mechanical fix).
- **Effort:** S-M (decision + guard) · **Priority:** P2 (decision) 
- **Depends on:** user decision.

## Quota-cost view from Phase 6 observations

- **What:** A per-model cost/quota-draw view built from the Phase 6
  observation store (candidate-limit deltas by model), replacing API-price
  display as the cost signal.
- **Why:** API per-token prices do not model subscription quota consumption —
  the exact confusion that started the 2026-08-16 incident. The observation
  store already collects the relevant data.
- **Pros:** Cost signal that matches the bill users actually experience.
- **Cons:** Needs the Phase 6 observation window to close (≥28 days / ≥30
  intervals) and its report to be evaluated first.
- **Context:** CEO F7 / DX F8 from the Phase 7 review; `summarize_quota_observations.py`
  already computes per-model deltas.
- **Effort:** S (human ~4h / CC ~30min) · **Priority:** P3
- **Depends on:** Phase 6 evaluation report (`decision_required`).
