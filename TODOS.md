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
