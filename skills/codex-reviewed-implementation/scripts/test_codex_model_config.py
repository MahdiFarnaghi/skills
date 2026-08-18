#!/usr/bin/env python3
"""Tests for codex_model_config.py — Phase 7 execution-profile resolution.

Hermetic: no Codex CLI, no network. Public-API seam only.
Run: `pytest scripts/test_codex_model_config.py` from the skill root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_model_config as cmc  # noqa: E402


# ---------------------------------------------------------------------------
# load_config — strict closed-schema TOML reading
# ---------------------------------------------------------------------------

def test_load_config_minimal_valid(tmp_path):
    cfg = tmp_path / ".codex-review.toml"
    cfg.write_text('version = 1\nmodel = "gpt-5.6-luna"\n', encoding="utf-8")
    loaded = cmc.load_config(str(cfg))
    assert loaded == {"version": 1, "model": "gpt-5.6-luna"}


def _expect_config_error(tmp_path, text, code_fragment):
    cfg = tmp_path / ".codex-review.toml"
    cfg.write_text(text, encoding="utf-8")
    try:
        cmc.load_config(str(cfg))
    except cmc.ConfigError as exc:
        assert code_fragment in str(exc), f"{code_fragment!r} not in: {exc}"
        assert exc.code.startswith("E_")
        return exc
    raise AssertionError("expected ConfigError")


def test_load_config_rejects_unknown_key(tmp_path):
    exc = _expect_config_error(
        tmp_path, 'version = 1\nmodel = "m"\n"reasoning-effort" = "low"\n', "unknown key"
    )
    assert "reasoning-effort" in str(exc)


def test_load_config_rejects_malformed_toml(tmp_path):
    _expect_config_error(tmp_path, "version = [unclosed\n", "not valid TOML")


def test_load_config_rejects_wrong_version(tmp_path):
    _expect_config_error(tmp_path, 'version = 2\nmodel = "m"\n', "version must be")
    # 1.0/true compare equal to 1 in Python but must NOT pass: they would
    # silently change the profile digest relative to the integer 1.
    _expect_config_error(tmp_path, 'version = 1.0\nmodel = "m"\n', "integer")
    _expect_config_error(tmp_path, 'version = true\nmodel = "m"\n', "integer")


def test_load_config_rejects_bad_effort(tmp_path):
    _expect_config_error(
        tmp_path, 'version = 1\nmodel = "m"\nreasoning_effort = "turbo"\n', "reasoning_effort"
    )


def test_load_config_rejects_bad_model_shape(tmp_path):
    _expect_config_error(
        tmp_path, 'version = 1\nmodel = "not a model!!"\n', "'model'"
    )
    # \Z anchors: a trailing newline must not slip through $-style matching
    _expect_config_error(tmp_path, 'version = 1\nmodel = "gpt-5\\n"\n', "'model'")


def test_load_config_rejects_missing_file(tmp_path):
    try:
        cmc.load_config(str(tmp_path / "absent.toml"))
    except cmc.ConfigError as exc:
        assert "cannot read config" in str(exc)
        return
    raise AssertionError("expected ConfigError")


# ---------------------------------------------------------------------------
# ExecutionProfile — canonical identity + digest
# ---------------------------------------------------------------------------

def _profile(**over):
    base = dict(
        model="gpt-5.6-luna", reasoning_effort="medium", codex_profile=None,
        config_version=1, resolution_source="project_config",
    )
    base.update(over)
    return cmc.ExecutionProfile(**base)


def test_profile_digest_stable_for_same_execution_fields():
    a = _profile(resolution_source="project_config")
    b = _profile(resolution_source="cli_flag")
    # resolution_source is provenance, not execution: identical digest
    assert a.digest == b.digest


def test_profile_digest_changes_with_execution_fields():
    a = _profile()
    assert a.digest != _profile(model="gpt-5.6-terra").digest
    assert a.digest != _profile(reasoning_effort="high").digest
    assert a.digest != _profile(codex_profile="review").digest
    assert a.digest != _profile(config_version="none").digest


def test_profile_receipt_fields_are_the_bound_keys():
    fields = _profile().receipt_fields()
    assert fields["model"] == "gpt-5.6-luna"
    assert fields["reasoning_effort"] == "medium"
    assert fields["config_version"] == 1
    assert fields["resolution_source"] == "project_config"
    assert fields["profile_digest"] == _profile().digest


def test_cli_default_normalizes_to_none():
    p = cmc.ExecutionProfile(
        model=cmc.CLI_DEFAULT, reasoning_effort=None, codex_profile=None,
        config_version="none", resolution_source="cli_flag",
    )
    assert p.model is None
    assert p.digest == _profile(
        model=None, reasoning_effort=None, codex_profile=None, config_version="none",
        resolution_source="whatever-source",
    ).digest


# ---------------------------------------------------------------------------
# resolve_profile — the precedence chain
# ---------------------------------------------------------------------------

def _wt(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    return str(wt)


def _user_home(tmp_path, model_pin=None):
    home = tmp_path / "codexhome"
    home.mkdir(exist_ok=True)
    if model_pin is not None:
        (home / "config.toml").write_text(f'model = "{model_pin}"\n', encoding="utf-8")
    return str(home)


def test_resolve_cli_flag_wins_over_project_config(tmp_path):
    wt = _wt(tmp_path)
    (Path(wt) / ".codex-review.toml").write_text(
        'version = 1\nmodel = "gpt-5.6-luna"\n', encoding="utf-8"
    )
    p = cmc.resolve_profile(
        cli_model="gpt-5.6-terra", worktree=wt, codex_home=_user_home(tmp_path),
    )
    assert p.model == "gpt-5.6-terra"
    assert p.resolution_source == "cli_flag"
    # project file still identifies the policy context
    assert p.config_version == 1


def test_resolve_project_config_used_when_no_flags(tmp_path):
    wt = _wt(tmp_path)
    (Path(wt) / ".codex-review.toml").write_text(
        'version = 1\nmodel = "gpt-5.6-luna"\nreasoning_effort = "medium"\n',
        encoding="utf-8",
    )
    p = cmc.resolve_profile(worktree=wt, codex_home=_user_home(tmp_path))
    assert p.model == "gpt-5.6-luna"
    assert p.reasoning_effort == "medium"
    assert p.resolution_source == "project_config"
    assert p.config_version == 1


def test_resolve_no_config_records_native_default(tmp_path):
    # fresh project, no flags, no project config, user pins a model:
    # resolution records WHERE the effective model comes from instead of
    # silently running it (the 2026-08-16 incident becomes diagnosable).
    p = cmc.resolve_profile(
        worktree=_wt(tmp_path), codex_home=_user_home(tmp_path, model_pin="gpt-5.6-sol"),
    )
    assert p.model is None  # no -m is passed; the CLI still chooses
    assert p.resolution_source == "user_config"
    assert p.config_version == "none"


def test_resolve_no_config_no_user_pin_records_cli_default(tmp_path):
    p = cmc.resolve_profile(
        worktree=_wt(tmp_path), codex_home=_user_home(tmp_path),
    )
    assert p.model is None
    assert p.resolution_source == "cli_default"
    assert p.config_version == "none"


def test_resolve_user_config_unreadable_falls_back_to_cli_default(tmp_path):
    # fail-soft: a corrupt user config.toml must not break resolution
    home = tmp_path / "brokenhome"
    home.mkdir()
    (home / "config.toml").write_text("not [valid toml\n", encoding="utf-8")
    p = cmc.resolve_profile(worktree=_wt(tmp_path), codex_home=str(home))
    assert p.resolution_source == "cli_default"


def test_resolve_require_config_without_any_config_fails_loud(tmp_path):
    try:
        cmc.resolve_profile(
            worktree=_wt(tmp_path), require_config=True,
            codex_home=_user_home(tmp_path),
        )
    except cmc.ConfigError as exc:
        assert exc.code == "E_CONFIG_MISSING"
        assert "init-config --worktree" in str(exc)
        return
    raise AssertionError("expected E_CONFIG_MISSING")


def test_resolve_require_config_accepts_cli_default_sentinel(tmp_path):
    # The remediation message itself recommends `--model cli-default`; it
    # must satisfy --require-config (the decision is pinned), even though the
    # sentinel normalizes model to None.
    p = cmc.resolve_profile(
        cli_model=cmc.CLI_DEFAULT, worktree=_wt(tmp_path), require_config=True,
        codex_home=_user_home(tmp_path),
    )
    assert p.model is None
    assert p.resolution_source == "cli_flag"


def test_resolve_binds_user_pin_value_not_just_presence(tmp_path):
    # Doctor certifies under pin A; the user config later changes to pin B.
    # The digest MUST differ, or the stale receipt authorizes the B review.
    def _home(name, pin):
        h = tmp_path / name; h.mkdir()
        (h / "config.toml").write_text(f'model = "{pin}"\n', encoding="utf-8")
        return str(h)

    home_a = _home("home-a", "gpt-5.6-sol")
    home_b = _home("home-b", "gpt-5.6-terra")
    a = cmc.resolve_profile(worktree=_wt(tmp_path), codex_home=home_a)
    b = cmc.resolve_profile(worktree=_wt(tmp_path), codex_home=home_b)
    assert a.native_model_pin == "gpt-5.6-sol"
    assert b.native_model_pin == "gpt-5.6-terra"
    assert a.digest != b.digest
    # a pinned -m makes the user pin irrelevant: no extra binding
    m = cmc.resolve_profile(cli_model="gpt-5.4", worktree=_wt(tmp_path), codex_home=home_a)
    assert m.native_model_pin is None


def test_resolve_rejects_bad_codex_profile_stably(tmp_path):
    try:
        cmc.resolve_profile(
            cli_model="gpt-5.6-luna", cli_codex_profile="bad profile",
            worktree=_wt(tmp_path), codex_home=_user_home(tmp_path),
        )
    except cmc.ConfigError as exc:
        assert "codex_profile" in str(exc)
        return
    raise AssertionError("expected ConfigError for invalid codex_profile")


def test_write_config_atomic_no_overwrite_under_race(tmp_path, monkeypatch):
    # Even if the early existence check is fooled (racy concurrent run), the
    # atomic link step must still refuse to replace without --force.
    path = str(tmp_path / ".codex-review.toml")
    cmc.write_config(path, model="gpt-5.6-luna")
    monkeypatch.setattr(os.path, "exists", lambda p: False)  # simulate the race
    try:
        cmc.write_config(path, model="gpt-5.6-terra")
    except cmc.ConfigError as exc:
        assert "E_CONFIG_EXISTS" in str(exc)
        assert cmc.load_config(path)["model"] == "gpt-5.6-luna"  # untouched
        return
    raise AssertionError("expected E_CONFIG_EXISTS despite fooled existence check")


def test_resolve_malformed_project_config_is_always_loud(tmp_path):
    wt = _wt(tmp_path)
    (Path(wt) / ".codex-review.toml").write_text("version = oops\n", encoding="utf-8")
    try:
        cmc.resolve_profile(worktree=wt, codex_home=_user_home(tmp_path))
    except cmc.ConfigError as exc:
        # never a silent fall-through to defaults
        assert "E_CONFIG_MALFORMED" in str(exc)
        return
    raise AssertionError("expected ConfigError")


def test_resolve_cli_effort_flag_only_keeps_project_model(tmp_path):
    wt = _wt(tmp_path)
    (Path(wt) / ".codex-review.toml").write_text(
        'version = 1\nmodel = "gpt-5.6-luna"\n', encoding="utf-8"
    )
    p = cmc.resolve_profile(
        cli_effort="high", worktree=wt, codex_home=_user_home(tmp_path),
    )
    assert p.model == "gpt-5.6-luna"
    assert p.reasoning_effort == "high"
    assert p.resolution_source == "cli_flag"


# ---------------------------------------------------------------------------
# write_config — closed-schema TOML emitter
# ---------------------------------------------------------------------------

def test_write_config_round_trips_through_load(tmp_path):
    path = str(tmp_path / ".codex-review.toml")
    cmc.write_config(path, model="gpt-5.6-luna", reasoning_effort="medium")
    loaded = cmc.load_config(path)
    assert loaded == {
        "version": 1, "model": "gpt-5.6-luna", "reasoning_effort": "medium",
    }


def test_write_config_rejects_invalid_values_before_writing(tmp_path):
    # The write seam validates values: init-config must never emit a config
    # its own loader rejects one command later.
    path = str(tmp_path / ".codex-review.toml")
    for bad_model in ('weird"\\model\nname', "x; touch pwned", "gpt-5\n"):
        try:
            cmc.write_config(path, model=bad_model)
        except cmc.ConfigError:
            assert not os.path.exists(path), "invalid value must not be written"
            continue
        raise AssertionError(f"expected ConfigError for model {bad_model!r}")
    try:
        cmc.write_config(path, model="gpt-5.6-luna", reasoning_effort="turbo")
    except cmc.ConfigError:
        assert not os.path.exists(path)
        return
    raise AssertionError("expected ConfigError for invalid effort")


def test_write_config_refuses_overwrite_without_force(tmp_path):
    path = str(tmp_path / ".codex-review.toml")
    cmc.write_config(path, model="gpt-5.6-luna")
    try:
        cmc.write_config(path, model="gpt-5.6-terra")
    except cmc.ConfigError as exc:
        assert "--force" in str(exc)
        assert cmc.load_config(path)["model"] == "gpt-5.6-luna"  # untouched
        return
    raise AssertionError("expected ConfigError for overwrite")


def test_write_config_force_overwrites(tmp_path):
    path = str(tmp_path / ".codex-review.toml")
    cmc.write_config(path, model="gpt-5.6-luna")
    cmc.write_config(path, model="gpt-5.6-terra", force=True)
    assert cmc.load_config(path)["model"] == "gpt-5.6-terra"


def test_write_config_writes_only_closed_schema_keys(tmp_path):
    path = str(tmp_path / ".codex-review.toml")
    cmc.write_config(path, model="gpt-5.6-luna", reasoning_effort="high",
                     codex_profile="review")
    text = Path(path).read_text(encoding="utf-8")
    for key in ("catalog", "metadata", "escalation", "fetched_at"):
        assert key not in text
    assert cmc.load_config(path)["codex_profile"] == "review"


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"]).returncode)
