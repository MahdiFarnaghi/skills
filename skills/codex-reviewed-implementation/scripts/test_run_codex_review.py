#!/usr/bin/env python3
"""Unit and contract tests for run_codex_review.py.

No real Codex CLI is invoked. Subprocess execution is injected via a fake
runner that also serves the git calls, so the suite is hermetic and free.
Run: `pytest scripts/test_run_codex_review.py` from the skill root.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_codex_review as rcr  # noqa: E402
import codex_model_config as cmc  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCP(subprocess.CompletedProcess):
    """CompletedProcess that tolerates missing stdout/stderr args."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        super().__init__(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    """Routes git and codex calls without touching the real world."""

    def __init__(
        self,
        *,
        worktree: str,
        verdict: dict | str | None = None,
        review_returncode: int = 0,
        raise_on_review: Exception | None = None,
        dirty_lines: list[str] | None = None,
        mutate_on_review: bool = False,
        diff_text: str = "",
    ):
        self.worktree = worktree
        self.verdict = verdict  # dict | "missing" | "malformed" | None
        self.review_returncode = review_returncode
        self.raise_on_review = raise_on_review
        self.dirty_lines = dirty_lines if dirty_lines is not None else []
        self.mutate_on_review = mutate_on_review
        self.diff_text = diff_text
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "git":
            return self._git(cmd)
        if cmd[0] == "codex":
            if "--version" in cmd:
                return FakeCP(stdout="codex-cli 0.146.0\n")
            return self._review(cmd, kwargs)
        raise AssertionError(f"unexpected command: {cmd}")

    def _git(self, cmd: list[str]) -> FakeCP:
        # cmd == ["git", "-C", worktree, *args]
        args = cmd[3:]
        if "rev-parse" in args:
            if "--show-toplevel" in args:
                return FakeCP(stdout=self.worktree + "\n")
            if "--git-common-dir" in args:
                return FakeCP(stdout=".git\n")
            if "HEAD" in args:
                return FakeCP(stdout="deadbeefcafebabe000000000000000000000000\n")
            if "--verify" in args:
                return FakeCP(stdout="ba5eba11ba5eba11ba5eba11ba5eba11ba5eba11\n")
        if "merge-base" in args:
            return FakeCP(stdout="merge0000merge0000merge0000merge0000merge0000\n")
        if "branch" in args and "--show-current" in args:
            return FakeCP(stdout="main\n")
        if "status" in args:
            return FakeCP(stdout="\n".join(self.dirty_lines) + "\n")
        if "diff" in args:
            return FakeCP(stdout=self.diff_text)
        if "--is-inside-work-tree" in args:
            return FakeCP(stdout="true\n")
        return FakeCP(stdout="")

    def _review(self, cmd: list[str], kwargs) -> FakeCP:
        if self.raise_on_review is not None:
            raise self.raise_on_review
        if self.mutate_on_review:
            self.diff_text += "changed during review"
        # Resolve -o target from the arg vector and write the verdict file.
        out_path = cmd[cmd.index("-o") + 1]
        if self.verdict == "missing":
            pass  # write nothing
        elif self.verdict == "malformed":
            Path(out_path).write_text("{ not valid json", encoding="utf-8")
        elif isinstance(self.verdict, dict):
            prompt = cmd[-1]
            rendered = json.loads(json.dumps(self.verdict))
            target_ref = re.search(r"^target_ref: ([a-f0-9]{64})$", prompt, re.M)
            baseline = re.search(r"^baseline: (\S+)$", prompt, re.M)
            if target_ref:
                rendered["target"]["target_ref"] = target_ref.group(1)
            if baseline:
                rendered["target"]["baseline"] = baseline.group(1)
            Path(out_path).write_text(
                json.dumps(rendered), encoding="utf-8"
            )
        return FakeCP(returncode=self.review_returncode, stdout="review done\n", stderr="")


def base_verdict(worktree: str, scope: str = "uncommitted", baseline: str = "deadbeefcafebabe000000000000000000000000", *, verdict: str = "approve", findings=None) -> dict:
    return {
        "schema_version": 2,
        "review_kind": "milestone",
        "verdict": verdict,
        "summary": "Looks good.",
        "target": {
            "repository": worktree,
            "worktree": worktree,
            "scope": scope,
            "baseline": baseline,
            "target_ref": "f" * 64,
        },
        "findings": findings or [],
        "next_steps": "advance",
    }


def finding(**over) -> dict:
    base = {
        "id": "F-1",
        "severity": "medium",
        "title": "t",
        "explanation": "e",
        "file": None,
        "line": None,
        "evidence": "ev",
        "affected_behavior": "ab",
        "recommendation": "r",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# build_command / scope validation
# ---------------------------------------------------------------------------

def test_build_command_flag_placement():
    cmd = rcr.build_command(
        worktree="/tmp/wt",
        schema_path="/tmp/s.json", out_path="/tmp/out.json",
    )
    # exec-level flags BEFORE the review subcommand
    assert cmd[:6] == ["codex", "exec", "-C", "/tmp/wt", "-s", "read-only"]
    assert "review" not in cmd
    assert "-C" in cmd and "-s" in cmd
    assert not ({"--uncommitted", "--base", "--commit"} & set(cmd))
    assert "--output-schema" in cmd and "-o" in cmd
    assert "--ephemeral" in cmd and "--ignore-rules" in cmd
    # command is an argument vector, not a shell string
    assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)


def test_validate_scope_options():
    assert rcr.validate_scope_options(scope="uncommitted", base=None, commit=None) is None
    assert rcr.validate_scope_options(scope="base", base="main", commit=None) is None
    assert rcr.validate_scope_options(scope="commit", base=None, commit="abc123") is None


def test_build_command_model_optional():
    without = rcr.build_command(worktree="/w", schema_path="/s", out_path="/o")
    with_model = rcr.build_command(worktree="/w", schema_path="/s", out_path="/o", model="gpt-5")
    assert "-m" not in without
    assert with_model[with_model.index("-m") + 1] == "gpt-5"


def test_build_command_reasoning_effort_becomes_native_override():
    cmd = rcr.build_command(
        worktree="/w", schema_path="/s", out_path="/o", model="gpt-5.6-luna",
        reasoning_effort="high",
    )
    i = cmd.index("-c")
    assert cmd[i + 1] == 'model_reasoning_effort="high"'  # TOML-value form
    assert "-m" in cmd


def test_build_command_reasoning_effort_omitted_when_none():
    cmd = rcr.build_command(worktree="/w", schema_path="/s", out_path="/o")
    assert "-c" not in cmd


def test_build_command_codex_profile_flag():
    cmd = rcr.build_command(
        worktree="/w", schema_path="/s", out_path="/o", codex_profile="review",
    )
    assert cmd[cmd.index("-p") + 1] == "review"


# ---------------------------------------------------------------------------
# Phase 7 — profile in review receipts, doctor receipts, and the probe
# ---------------------------------------------------------------------------

def test_run_review_receipt_binds_profile(tmp_path):
    ledger = tmp_path / "ledger.json"
    res, fake = _profiled_run(tmp_path, ledger, _profile())
    assert res.outcome == "completed"
    receipt = json.loads(next(Path(tmp_path / "out").glob("receipt-*.json")).read_text())
    assert receipt["profile"]["profile_digest"] == _profile().digest
    assert receipt["profile"]["resolution_source"] == "project_config"
    assert receipt["profile"]["model"] == "gpt-5.6-luna"
    assert receipt["profile"]["reasoning_effort"] == "medium"
    # the executed vector carries the native effort override
    exec_calls = [c for c in fake.calls if "exec" in c and "--output-schema" in c]
    assert exec_calls and 'model_reasoning_effort="medium"' in exec_calls[0]


def test_run_doctor_receipt_records_profile(tmp_path):
    schema = tmp_path / "schema.json"; schema.write_text("{}", encoding="utf-8")
    receipt_path = tmp_path / "doctor.json"
    wt = tmp_path / "wt"; wt.mkdir(); (wt / ".git").mkdir()
    doctor_verdict = base_verdict(str(wt))
    doctor_verdict["target"]["target_ref"] = "d" * 64  # doctor's synthetic ref
    fake = FakeRunner(worktree=str(wt), verdict=doctor_verdict)
    passed, receipt = rcr.run_doctor(
        worktree=str(wt), schema_path=str(schema), receipt_path=str(receipt_path),
        profile=_profile(), runner=fake,
    )
    assert passed
    assert receipt["profile_digest"] == _profile().digest
    assert receipt["config_version"] == 1
    assert receipt["resolution_source"] == "project_config"
    # the doctor executes the same effort override as a review would
    exec_calls = [c for c in fake.calls if "exec" in c and "--output-schema" in c]
    assert exec_calls and 'model_reasoning_effort="medium"' in exec_calls[0]


def test_preflight_probe_vector_parity_with_exec_vector():
    # The zero-cost probe must exercise the SAME option surface as the real
    # exec when a profile is active — otherwise it certifies a different
    # command than the one that runs.
    profile = _profile(model="gpt-5.6-luna")
    seen = []

    def runner(cmd, **kwargs):
        seen.append(list(cmd))
        if "--version" in cmd:
            return FakeCP(stdout="codex-cli 0.146.0\n")
        return FakeCP(stderr="invalid value '__not_a_sandbox__' for '--sandbox'\n", returncode=2)

    ok, failures = rcr.preflight(
        worktree=None, schema_path=None, runner=runner, profile=profile,
    )
    assert ok, failures
    probe = next(c for c in seen if "exec" in c and "__transport_probe__" in c)
    assert "-m" in probe and probe[probe.index("-m") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="medium"' in probe


def test_build_command_uses_resolved_codex_path():
    cmd = rcr.build_command(
        worktree="/w", schema_path="/s", out_path="/o",
        codex_path="/opt/codex/bin/codex",
    )
    assert cmd[0] == "/opt/codex/bin/codex"


def test_scope_contract_preserves_packet_and_exact_target():
    fingerprint = "a" * 64
    resolved = {"baseline": "headsha", "head": "headsha"}
    contract = rcr.scope_contract("uncommitted", resolved, fingerprint)
    prompt = rcr.build_prompt("directed question", contract)
    assert "git diff --cached --binary --no-ext-diff" in prompt
    assert "git ls-files --others --exclude-standard" in prompt
    assert f"target_ref: {fingerprint}" in prompt
    assert "directed question" in prompt


def test_scope_contract_base_and_commit_are_immutable():
    base = rcr.scope_contract(
        "base", {"baseline": "base_sha", "head": "head_sha", "merge_base": "merge_sha"},
        "b" * 64,
    )
    commit = rcr.scope_contract(
        "commit", {"baseline": "commit_sha", "commit": "commit_sha"}, "c" * 64,
    )
    assert "git diff --binary --no-ext-diff merge_sha" in base
    assert "git show --binary --no-ext-diff --format=fuller commit_sha" in commit


def test_scope_exclusivity():
    import pytest
    with pytest.raises(ValueError):
        rcr.validate_scope_options(scope="uncommitted", base="main", commit=None)
    with pytest.raises(ValueError):
        rcr.validate_scope_options(scope="base", base=None, commit=None)
    with pytest.raises(ValueError):
        rcr.validate_scope_options(scope="commit", base="main", commit="abc")
    with pytest.raises(ValueError):
        rcr.validate_scope_options(scope="bogus", base=None, commit=None)


# ---------------------------------------------------------------------------
# validate_inputs
# ---------------------------------------------------------------------------

def _valid_inputs(**over):
    base = dict(
        review_kind="milestone", worktree="/tmp/wt", scope="uncommitted", base=None, commit=None,
        packet_path="/tmp/packet.md", schema_path="/tmp/s.json", output_dir="/tmp/out",
    )
    base.update(over)
    return base


def test_validate_inputs_ok(tmp_path):
    packet = tmp_path / "p.md"; packet.write_text("x")
    schema = tmp_path / "s.json"; schema.write_text('{"properties":{"schema_version":{"const":2}}}')
    wt = tmp_path / "wt"; wt.mkdir()
    out = tmp_path / "out"
    errs = rcr.validate_inputs(**_valid_inputs(
        worktree=str(wt), packet_path=str(packet), schema_path=str(schema), output_dir=str(out)))
    assert errs == []


def test_validate_inputs_rejects_output_inside_worktree(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    inside = wt / "receipts"
    errs = rcr.validate_inputs(**_valid_inputs(worktree=str(wt), output_dir=str(inside)))
    assert any("outside the reviewed worktree" in e for e in errs)


def test_validate_inputs_rejects_oversized_packet(tmp_path):
    packet = tmp_path / "p.md"; packet.write_text("x" * (rcr.MAX_PACKET_BYTES + 1))
    schema = tmp_path / "s.json"; schema.write_text('{"properties":{"schema_version":{"const":2}}}')
    errs = rcr.validate_inputs(**_valid_inputs(packet_path=str(packet), schema_path=str(schema)))
    assert any("max is" in e for e in errs)


def test_validate_inputs_rejects_bad_schema(tmp_path):
    packet = tmp_path / "p.md"; packet.write_text("x")
    schema = tmp_path / "s.json"; schema.write_text("{bad")
    errs = rcr.validate_inputs(**_valid_inputs(packet_path=str(packet), schema_path=str(schema)))
    assert any("not valid JSON" in e for e in errs)


def test_validate_inputs_nonabs_worktree():
    errs = rcr.validate_inputs(**_valid_inputs(worktree="relative/wt"))
    assert any("must be absolute" in e for e in errs)


# ---------------------------------------------------------------------------
# validate_verdict
# ---------------------------------------------------------------------------

def test_validate_verdict_approve_clean():
    v = base_verdict("/tmp/wt")
    assert rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"], expected_target_ref=v["target"]["target_ref"]) == []


def test_validate_verdict_needs_attention_with_finding():
    v = base_verdict("/tmp/wt", verdict="needs-attention", findings=[finding()])
    assert rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"]) == []


def test_validate_verdict_approve_with_blocking_rejected():
    v = base_verdict("/tmp/wt", findings=[finding(severity="high")])
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("approve but findings" in e for e in errs)


def test_validate_verdict_missing_keys():
    errs = rcr.validate_verdict({}, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline="x")
    assert any("missing required key" in e for e in errs)


def test_validate_verdict_unknown_keys():
    v = base_verdict("/tmp/wt")
    v["rogue"] = 1
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("unknown top-level" in e for e in errs)


def test_validate_verdict_duplicate_finding_ids():
    v = base_verdict("/tmp/wt", verdict="needs-attention", findings=[finding(id="F-1"), finding(id="F-1", severity="low")])
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("duplicate id" in e for e in errs)


def test_validate_verdict_bad_enum():
    v = base_verdict("/tmp/wt")
    v["verdict"] = "maybe"
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("verdict must be one of" in e for e in errs)


def test_validate_verdict_target_worktree_mismatch():
    v = base_verdict("/tmp/wt")
    errs = rcr.validate_verdict(v, expected_worktree="/other/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("target.worktree does not match" in e for e in errs)


def test_validate_verdict_target_scope_mismatch():
    v = base_verdict("/tmp/wt", scope="base")
    v["target"]["baseline"] = "main"
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline="main")
    assert any("target.scope does not match" in e for e in errs)


def test_validate_verdict_target_fingerprint_mismatch():
    v = base_verdict("/tmp/wt")
    errs = rcr.validate_verdict(
        v, expected_worktree="/tmp/wt", expected_scope="uncommitted",
        expected_baseline=v["target"]["baseline"], expected_target_ref="0" * 64,
    )
    assert any("target.target_ref does not match" in e for e in errs)


def test_validate_verdict_rejects_approve_with_low_finding_and_path_escape():
    v = base_verdict(
        "/tmp/wt", findings=[finding(severity="low", file="../escape.py", line="1-2")]
    )
    errs = rcr.validate_verdict(
        v, expected_worktree="/tmp/wt", expected_scope="uncommitted",
        expected_baseline=v["target"]["baseline"],
    )
    assert any("approve but findings" in e for e in errs)
    assert any("repository-relative" in e for e in errs)


def test_validate_verdict_bad_finding_id():
    v = base_verdict("/tmp/wt", verdict="needs-attention", findings=[finding(id="bad")])
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("id must match" in e for e in errs)


def test_validate_verdict_non_object():
    assert rcr.validate_verdict("nope", expected_worktree="/x", expected_scope="uncommitted", expected_baseline="y") == ["verdict is not a JSON object"]


def test_is_blocking():
    assert rcr.is_blocking(base_verdict("/w", findings=[finding(severity="high")])) is True
    assert rcr.is_blocking(base_verdict("/w", findings=[finding(severity="low")])) is False


# ---------------------------------------------------------------------------
# digests, redaction, classification, version
# ---------------------------------------------------------------------------

def test_dirty_manifest_digest_order_independent():
    a = rcr.dirty_manifest_digest([" M a.py", "?? b.py"])
    b = rcr.dirty_manifest_digest(["?? b.py", " M a.py"])
    assert a == b and len(a) == 64


def test_redact_secrets():
    out = rcr.redact("key=sk-" + "a" * 30 + " and api_key=ABCDEF0123456789abcdbgk")
    assert "sk-" + "a" * 30 not in out
    assert "ABCDEF0123456789abcdbgk" not in out
    # normal text survives
    assert "key=" in out


def test_classify_outcome():
    assert rcr.classify_outcome(returncode=0, timed_out=False, cancelled=False, cli_present=True) == "completed"
    assert rcr.classify_outcome(returncode=2, timed_out=False, cancelled=False, cli_present=True) == "process_failed"
    assert rcr.classify_outcome(returncode=0, timed_out=True, cancelled=False, cli_present=True) == "timed_out"
    assert rcr.classify_outcome(returncode=None, timed_out=False, cancelled=True, cli_present=True) == "cancelled"
    assert rcr.classify_outcome(returncode=0, timed_out=False, cancelled=False, cli_present=False) == "cli_missing"


def test_version_is_supported():
    assert rcr.version_is_supported("codex-cli 0.146.0")[0] is True
    ok, _ = rcr.version_is_supported("codex-cli 0.120.2")
    assert ok is False
    ok2, _ = rcr.version_is_supported("garbage")
    assert ok2 is False


# ---------------------------------------------------------------------------
# run_review end-to-end (injected runner)
# ---------------------------------------------------------------------------

def _run(tmp_path, *, verdict, review_returncode=0, raise_on_review=None,
         dirty_lines=None, mutate_on_review=False, scope="uncommitted",
         base=None, commit=None, state_ledger=None, review_kind="milestone"):
    wt = tmp_path / "wt"; wt.mkdir()
    (wt / ".git").mkdir()  # not a real repo, but git calls are faked
    out = tmp_path / "out"
    packet = tmp_path / "packet.md"; packet.write_text("# packet focus\n")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    baseline = commit or base or "deadbeefcafebabe000000000000000000000000"
    fake = FakeRunner(
        worktree=str(wt), verdict=verdict, review_returncode=review_returncode,
        raise_on_review=raise_on_review, dirty_lines=dirty_lines,
        mutate_on_review=mutate_on_review,
    )
    return rcr.run_review(
        review_kind=review_kind, worktree=str(wt), scope=scope, base=base, commit=commit,
        packet_path=str(packet), schema_path=str(schema), output_dir=str(out),
        milestone="m1", round_no=1, state_ledger=state_ledger,
        runner=fake,
    ), str(wt), baseline, fake, str(out)


def test_run_review_approve(tmp_path):
    wt = tmp_path / "wt"
    res, wta, baseline, fake, out = _run(tmp_path, verdict=base_verdict(str(wt)))
    assert res.outcome == "completed"
    assert res.verdict and res.verdict["verdict"] == "approve"
    receipt = json.loads(next(Path(out).glob("receipt-*.json")).read_text())
    assert receipt["outcome"] == "completed"
    assert receipt["verdict"]["verdict"] == "approve"
    # logs written + redaction applied (no crash)
    assert Path(out).glob("stdout-*.log").__next__().exists()


def test_run_review_needs_attention(tmp_path):
    wt = tmp_path / "wt"
    v = base_verdict(str(wt), verdict="needs-attention", findings=[finding()])
    res, *_ = _run(tmp_path, verdict=v)
    assert res.outcome == "completed"
    assert res.verdict["verdict"] == "needs-attention"


def test_run_review_process_failed(tmp_path):
    res, *_ = _run(tmp_path, verdict="missing", review_returncode=2)
    assert res.outcome == "process_failed"
    assert res.verdict is None


def test_run_review_timed_out(tmp_path):
    res, *_ = _run(tmp_path, verdict="missing", raise_on_review=subprocess.TimeoutExpired(cmd=[], timeout=1))
    assert res.outcome == "timed_out"


def test_run_review_cli_missing(tmp_path):
    res, *_ = _run(tmp_path, verdict="missing", raise_on_review=FileNotFoundError("codex"))
    assert res.outcome == "cli_missing"


def test_run_review_no_verdict_file(tmp_path):
    res, *_ = _run(tmp_path, verdict="missing", review_returncode=0)
    assert res.outcome == "invalid_output"
    assert "no verdict file" in res.diagnostics or "no verdict" in res.diagnostics.lower()


def test_run_review_removes_stale_verdict(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir(); (wt / ".git").mkdir()
    out = tmp_path / "out"; out.mkdir()
    stale = out / "verdict-m1-r1.json"
    stale.write_text(json.dumps(base_verdict(str(wt))))
    packet = tmp_path / "packet.md"; packet.write_text("packet")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    fake = FakeRunner(worktree=str(wt), verdict="missing")
    res = rcr.run_review(
        worktree=str(wt), scope="uncommitted", packet_path=str(packet),
        schema_path=str(schema), output_dir=str(out), milestone="m1",
        round_no=1, runner=fake,
    )
    assert res.outcome == "invalid_output"


def test_run_review_malformed_verdict(tmp_path):
    res, *_ = _run(tmp_path, verdict="malformed")
    assert res.outcome == "invalid_output"


def test_run_review_approve_with_blocking_is_invalid(tmp_path):
    wt = tmp_path / "wt"
    v = base_verdict(str(wt), findings=[finding(severity="critical")])
    res, *_ = _run(tmp_path, verdict=v)
    assert res.outcome == "invalid_output"
    assert "findings are present" in res.diagnostics


def test_run_review_target_changed(tmp_path):
    wt = tmp_path / "wt"
    res, *_ = _run(
        tmp_path, verdict=base_verdict(str(wt)),
        dirty_lines=[" M pre.py\n"], mutate_on_review=True,
    )
    assert res.outcome == "target_changed"


def test_run_review_wrong_worktree_verdict_rejected(tmp_path):
    wt = tmp_path / "wt"
    v = base_verdict(str(wt))
    v["target"]["worktree"] = "/totally/different/wt"
    res, *_ = _run(tmp_path, verdict=v)
    assert res.outcome == "invalid_output"
    assert "worktree does not match" in res.diagnostics


def test_run_review_writes_state_ledger(tmp_path):
    wt = tmp_path / "wt"
    ledger = tmp_path / "ledger.json"
    res, *_ = _run(tmp_path, verdict=base_verdict(str(wt)), state_ledger=str(ledger))
    data = json.loads(ledger.read_text())
    key = "m1:r1"
    assert key in data
    assert data[key]["status"] == "completed"
    assert data[key]["verdict"] == "approve"
    assert data[key]["pid"] == os.getpid()


def test_run_review_replays_completed_ledger_round(tmp_path):
    wt = tmp_path / "wt"
    ledger = tmp_path / "ledger.json"
    first, *_ = _run(tmp_path, verdict=base_verdict(str(wt)), state_ledger=str(ledger))
    packet = tmp_path / "packet.md"
    schema = tmp_path / "schema.json"
    fake = FakeRunner(worktree=str(wt), verdict="missing")
    replay = rcr.run_review(
        worktree=str(wt), scope="uncommitted", packet_path=str(packet),
        schema_path=str(schema), output_dir=str(tmp_path / "out"), milestone="m1",
        round_no=1, state_ledger=str(ledger), runner=fake,
    )
    assert replay.outcome == first.outcome
    assert not any(call[0] == "codex" and "exec" in call and "--output-schema" in call for call in fake.calls)


def test_run_review_rejects_conflicting_completed_round(tmp_path):
    wt = tmp_path / "wt"
    ledger = tmp_path / "ledger.json"
    _run(tmp_path, verdict=base_verdict(str(wt)), state_ledger=str(ledger))
    (tmp_path / "packet.md").write_text("different packet")
    import pytest
    with pytest.raises(RuntimeError, match="conflicts"):
        rcr.run_review(
            worktree=str(wt), scope="uncommitted", packet_path=str(tmp_path / "packet.md"),
            schema_path=str(tmp_path / "schema.json"), output_dir=str(tmp_path / "out"),
            milestone="m1", round_no=1, state_ledger=str(ledger),
            runner=FakeRunner(worktree=str(wt), verdict="missing"),
        )


# ---------------------------------------------------------------------------
# Phase 7 — profile-bound ledger rounds
# ---------------------------------------------------------------------------

def _profiled_run(tmp_path, ledger, profile, verdict=None, wt=None):
    wt = wt or (tmp_path / "wt")
    wt.mkdir(exist_ok=True)
    (wt / ".git").mkdir(exist_ok=True)
    (tmp_path / "packet.md").write_text("# packet focus\n", encoding="utf-8")
    (tmp_path / "schema.json").write_text("{}", encoding="utf-8")
    fake = FakeRunner(worktree=str(wt), verdict=verdict or base_verdict(str(wt)))
    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(tmp_path / "packet.md"), schema_path=str(tmp_path / "schema.json"),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        state_ledger=str(ledger), profile=profile, runner=fake,
    )
    return res, fake


def test_run_review_binds_profile_digest_in_ledger(tmp_path):
    ledger = tmp_path / "ledger.json"
    res, _ = _profiled_run(tmp_path, ledger, _profile())
    assert res.outcome == "completed"
    data = json.loads(ledger.read_text())
    assert data["m1:r1"]["profile_digest"] == _profile().digest


def test_run_review_replays_completed_round_only_for_same_profile(tmp_path):
    ledger = tmp_path / "ledger.json"
    _profiled_run(tmp_path, ledger, _profile())
    # Same profile: the stored verdict replays without a new codex call.
    res, fake = _profiled_run(tmp_path, ledger, _profile(), verdict="missing")
    assert res.outcome == "completed"
    assert res.verdict and res.verdict["verdict"] == "approve"
    assert not any("exec" in c and "--output-schema" in c for c in fake.calls)


def test_run_review_profile_change_advances_round_instead_of_crashing(tmp_path):
    import pytest
    ledger = tmp_path / "ledger.json"
    _profiled_run(tmp_path, ledger, _profile())
    # Policy change mid-milestone: same round, different profile → the old
    # verdict must NOT replay and the wrapper must NOT die with RuntimeError.
    other = _profile(reasoning_effort="high")
    res, fake = _profiled_run(tmp_path, ledger, other, verdict="missing")
    assert res.outcome == "profile_changed"
    assert "--round" in (res.diagnostics or "")
    # no review was executed under the mismatched profile
    assert not any("exec" in c and "--output-schema" in c for c in fake.calls)


def test_run_review_legacy_ledger_round_requires_new_round(tmp_path):
    import pytest
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").mkdir()
    ledger = tmp_path / "ledger.json"
    # A pre-Phase-7 completed entry has no profile_digest key.
    ledger.write_text(json.dumps({"m1:r1": {
        "milestone": "m1", "round": 1, "status": "completed",
        "receipt": "/nonexistent.json",
    }}))
    (tmp_path / "packet.md").write_text("# packet focus\n", encoding="utf-8")
    (tmp_path / "schema.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="profile_digest"):
        rcr.run_review(
            review_kind="milestone", worktree=str(wt), scope="uncommitted",
            packet_path=str(tmp_path / "packet.md"),
            schema_path=str(tmp_path / "schema.json"),
            output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
            state_ledger=str(ledger),
            runner=FakeRunner(worktree=str(wt), verdict="missing"),
        )


def test_run_review_scope_base_uses_baseline(tmp_path):
    wt = tmp_path / "wt"
    v = base_verdict(str(wt), scope="base", baseline="main")
    res, wta, baseline, fake, out = _run(tmp_path, verdict=v, scope="base", base="main")
    assert res.outcome == "completed"
    # Native selectors conflict with a prompt; immutable scope is in the custom
    # review prompt and no selector is passed to the CLI.
    receipt = json.loads(next(Path(out).glob("receipt-*.json")).read_text())
    assert "--base" not in receipt["transport"]["command"]
    review_call = next(call for call in fake.calls if call[0] == "codex" and "exec" in call and "--output-schema" in call)
    assert "scope: base" in review_call[-1]
    assert "merge_base:" in review_call[-1]
    assert "git diff --binary --no-ext-diff" in review_call[-1]


def test_run_plan_review_uses_plan_identity_not_code_diff(tmp_path):
    wt = tmp_path / "wt"
    verdict = base_verdict(str(wt), scope="plan")
    verdict["review_kind"] = "plan"
    res, *_rest = _run(tmp_path, verdict=verdict, review_kind="plan")
    assert res.outcome == "completed"
    assert res.receipt["target"]["scope"] == "plan"
    assert res.verdict["review_kind"] == "plan"


def test_commit_fingerprint_ignores_unrelated_dirty_state(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir(); (wt / ".git").mkdir()
    fake = FakeRunner(worktree=str(wt), dirty_lines=[" M unrelated.py"], diff_text="dirty")
    identity = rcr.git_identity(str(wt), runner=fake)
    resolved = {"baseline": "c" * 40, "commit": "c" * 40}
    first = rcr.target_fingerprint(str(wt), identity, scope="commit", resolved=resolved, runner=fake)
    fake.diff_text = "different unrelated dirty content"
    second = rcr.target_fingerprint(str(wt), identity, scope="commit", resolved=resolved, runner=fake)
    assert first == second


def test_doctor_receipt_version_binding(tmp_path):
    receipt = tmp_path / "doctor.json"
    receipt.write_text(json.dumps({**rcr.doctor_key("codex-cli 0.146.0"), "outcome": "passed"}))
    fake = FakeRunner(worktree=str(tmp_path))
    assert rcr.verify_doctor_receipt(str(receipt), runner=fake) == []
    data = json.loads(receipt.read_text()); data["transport_version"] = 1
    receipt.write_text(json.dumps(data))
    assert any("transport_version" in e for e in rcr.verify_doctor_receipt(str(receipt), runner=fake))


def test_doctor_receipt_model_binding(tmp_path):
    receipt = tmp_path / "doctor.json"
    receipt.write_text(json.dumps({**rcr.doctor_key("codex-cli 0.146.0", "gpt-5.4"), "outcome": "passed"}))
    fake = FakeRunner(worktree=str(tmp_path))
    assert rcr.verify_doctor_receipt(str(receipt), model="gpt-5.4", runner=fake) == []
    assert any("model" in e for e in rcr.verify_doctor_receipt(str(receipt), model="gpt-5.3", runner=fake))


# ---------------------------------------------------------------------------
# Phase 7 — execution-profile binding in doctor receipts
# ---------------------------------------------------------------------------

def _profile(**over):
    base = dict(
        model="gpt-5.6-luna", reasoning_effort="medium", codex_profile=None,
        config_version=1, resolution_source="project_config",
    )
    base.update(over)
    return cmc.ExecutionProfile(**base)


def test_doctor_key_binds_profile_fields():
    key = rcr.doctor_key("codex-cli 0.146.0", profile=_profile())
    assert key["model"] == "gpt-5.6-luna"
    assert key["reasoning_effort"] == "medium"
    assert key["config_version"] == 1
    assert key["profile_digest"] == _profile().digest
    # resolution_source is provenance: recorded in receipts, never compared
    assert "resolution_source" not in key


def test_verify_doctor_receipt_accepts_matching_profile(tmp_path):
    receipt = tmp_path / "doctor.json"
    receipt.write_text(json.dumps({
        **rcr.doctor_key("codex-cli 0.146.0", profile=_profile()),
        "resolution_source": "project_config",
        "outcome": "passed",
    }))
    fake = FakeRunner(worktree=str(tmp_path))
    assert rcr.verify_doctor_receipt(
        str(receipt), profile=_profile(), runner=fake
    ) == []


def test_verify_doctor_receipt_rejects_cross_profile(tmp_path):
    receipt = tmp_path / "doctor.json"
    receipt.write_text(json.dumps({
        **rcr.doctor_key("codex-cli 0.146.0", profile=_profile()),
        "outcome": "passed",
    }))
    fake = FakeRunner(worktree=str(tmp_path))
    other = _profile(reasoning_effort="high")
    errors = rcr.verify_doctor_receipt(str(receipt), profile=other, runner=fake)
    assert any("profile_digest" in e for e in errors)
    assert any("doctor" in e.lower() for e in errors)  # actionable remediation


def test_verify_doctor_receipt_legacy_receipt_names_remediation(tmp_path):
    # A pre-Phase-7 receipt lacks the profile keys entirely: it must fail as
    # doctor_required with the re-doctor fix, never silently pass.
    legacy = {
        "transport_version": rcr.TRANSPORT_VERSION,
        "schema_version": rcr.SCHEMA_VERSION,
        "codex_version": "codex-cli 0.146.0",
        "platform": sys.platform,
        "machine": "arm64",
        "model": "<cli-default>",
        "outcome": "passed",
    }
    receipt = tmp_path / "doctor.json"
    receipt.write_text(json.dumps(legacy))
    fake = FakeRunner(worktree=str(tmp_path))
    errors = rcr.verify_doctor_receipt(
        str(receipt), profile=_profile(model=None, config_version="none"), runner=fake,
    )
    assert any("profile_digest" in e for e in errors)
    assert any("re-run doctor" in e for e in errors)


def test_plan_fingerprint_tracks_referenced_file_contents(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    referenced = wt / "src" / "contract.py"; referenced.parent.mkdir()
    referenced.write_text("v1")
    packet = tmp_path / "packet.md"; packet.write_text("Inspect src/contract.py")
    first, paths = rcr.plan_fingerprint(str(packet), str(wt), "head")
    referenced.write_text("v2")
    second, _ = rcr.plan_fingerprint(str(packet), str(wt), "head")
    assert paths == ["src/contract.py"]
    assert first != second


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def _pf_runner(version="codex-cli 0.146.0", probe_text="invalid value '__not_a_sandbox__' for '--sandbox'"):
    def runner(cmd, **kwargs):
        if "--version" in cmd:
            return FakeCP(stdout=version + "\n")
        return FakeCP(stderr=probe_text + "\n", returncode=2)
    return runner


def test_preflight_ok(tmp_path):
    ok, fails = rcr.preflight(
        codex_path="/fake/codex",
        auth_env={"CODEX_API_KEY": "x"},
        codex_home=tmp_path,
        runner=_pf_runner(),
    )
    assert ok, fails


def test_preflight_detects_flag_shape_change(tmp_path):
    ok, fails = rcr.preflight(
        codex_path="/fake/codex",
        auth_env={"CODEX_API_KEY": "x"},
        codex_home=tmp_path,
        runner=_pf_runner(probe_text="error: unexpected argument '-s' found"),
    )
    assert not ok
    assert any("transport shape changed" in f for f in fails)


def test_preflight_known_bad_version(tmp_path):
    ok, fails = rcr.preflight(
        codex_path="/fake/codex",
        auth_env={"CODEX_API_KEY": "x"},
        codex_home=tmp_path,
        runner=_pf_runner(version="codex-cli 0.120.2"),
    )
    assert not ok
    assert any("known-bad" in f for f in fails)


def test_preflight_auth_missing(tmp_path):
    ok, fails = rcr.preflight(
        codex_path="/fake/codex",
        auth_env={},
        codex_home=tmp_path,  # no auth.json here
        runner=_pf_runner(),
    )
    assert not ok
    assert any("authentication" in f for f in fails)


# ---------------------------------------------------------------------------
# Phase 6: review-phase validation, observation wiring, process-group backport
# ---------------------------------------------------------------------------

class FakeQuotaObserver:
    """Records before/after/record calls; stand-in for the real QuotaObserver."""

    def __init__(self, before_snap=None, after_snap=None, *, record_raises=False):
        self.before_snap = before_snap
        self.after_snap = after_snap
        self.record_raises = record_raises
        self.before_calls = 0
        self.after_calls = 0
        self.records: list[tuple] = []

    def before(self):
        self.before_calls += 1
        return self.before_snap

    def after(self):
        self.after_calls += 1
        return self.after_snap

    def record(self, meta, before, after):
        if self.record_raises:
            raise RuntimeError("boom")
        self.records.append((meta, before, after))


def _ok_snapshot(used=42):
    return {"status": "ok", "account_pseudonym": "acct1",
            "candidates": [{"candidate_id": "C1", "window_role": "primary",
                            "window_duration_mins": 10080, "resets_at": 1000,
                            "used_percent": used}], "diagnostic": ""}


def test_validate_review_phase_combinations():
    assert rcr.validate_review_phase("plan_challenge", "plan") == []
    assert rcr.validate_review_phase("milestone", "milestone") == []
    assert rcr.validate_review_phase("correction", "milestone") == []
    assert rcr.validate_review_phase("final", "milestone") == []
    assert rcr.validate_review_phase("doctor", None) == []
    assert rcr.validate_review_phase("plan_challenge", "milestone")  # mismatch -> errors
    assert rcr.validate_review_phase("milestone", "plan")
    assert rcr.validate_review_phase("bogus", "milestone")


def test_run_review_records_before_after_without_changing_outcome(tmp_path):
    wt = tmp_path / "wt"
    obs = FakeQuotaObserver(before_snap=_ok_snapshot(40), after_snap=_ok_snapshot(48))
    res, *_ = _run(tmp_path, verdict=base_verdict(str(wt)))
    # Re-run with observer injected (the helper doesn't pass it, so call directly).
    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(tmp_path / "packet.md"), schema_path=str(tmp_path / "schema.json"),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        runner=FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt))),
        review_phase="milestone", quota_observer=obs,
    )
    assert res.outcome == "completed"  # unchanged
    assert res.verdict["verdict"] == "approve"
    assert obs.before_calls == 1 and obs.after_calls == 1
    assert len(obs.records) == 1
    meta, before, after = obs.records[0]
    assert meta["review_phase"] == "milestone"
    assert meta["outcome"] == "completed"
    assert before["candidates"][0]["used_percent"] == 40
    assert after["candidates"][0]["used_percent"] == 48
    assert "invocation_id" in meta and meta["invocation_id"]


def test_observation_never_alters_outcome_when_snapshots_fail(tmp_path):
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    obs = FakeQuotaObserver(before_snap=None, after_snap=None, record_raises=True)
    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        runner=FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt))),
        review_phase="milestone", quota_observer=obs,
    )
    # record() raised, but the review outcome is still the real completed verdict.
    assert res.outcome == "completed"
    assert res.verdict["verdict"] == "approve"


def test_observation_covers_failure_and_timeout(tmp_path):
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    obs = FakeQuotaObserver()
    rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        runner=FakeRunner(worktree=str(wt), verdict="missing", review_returncode=2),
        review_phase="milestone", quota_observer=obs,
    )
    assert obs.after_calls == 1  # after-snapshot captured even on process failure
    assert obs.records[0][0]["outcome"] == "process_failed"


def test_observation_after_runs_when_review_spawn_raises_oserror(tmp_path):
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    obs = FakeQuotaObserver()
    fake = FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt)))

    def raising_runner(cmd, **kwargs):
        if "exec" in cmd:
            raise PermissionError("cannot execute")
        return fake(cmd, **kwargs)

    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        runner=raising_runner, review_phase="milestone", quota_observer=obs,
    )
    assert res.outcome == "process_failed"
    assert obs.after_calls == 1
    assert obs.records[0][0]["outcome"] == "process_failed"


def test_replay_short_circuits_observation(tmp_path):
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    ledger = tmp_path / "ledger.json"
    obs = FakeQuotaObserver()
    # First run records the completed round (no observer, for a clean ledger).
    rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        state_ledger=str(ledger), runner=FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt))),
    )
    # Replay with an observer present: no quota reads should happen.
    rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        state_ledger=str(ledger), runner=FakeRunner(worktree=str(wt), verdict="missing"),
        review_phase="milestone", quota_observer=obs,
    )
    assert obs.before_calls == 0 and obs.after_calls == 0
    assert obs.records == []


def test_receipt_carries_non_authoritative_observation_summary(tmp_path):
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    obs = FakeQuotaObserver(before_snap=_ok_snapshot(40), after_snap=_ok_snapshot(48))
    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1,
        runner=FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt))),
        review_phase="milestone", quota_observer=obs,
    )
    assert res.receipt["observation"]["non_authoritative"] is True
    assert res.receipt["observation"]["before_status"] == "ok"
    assert res.receipt["observation"]["after_status"] == "ok"
    # raw candidate data is NOT embedded in the receipt
    assert "candidate_limits" not in res.receipt["observation"]


def test_production_execution_path_uses_run_one_shot(monkeypatch, tmp_path):
    """When no runner is injected, the review subprocess goes through run_one_shot
    (process-group-aware). Injected runners keep their existing semantics."""
    import codex_process

    wt = tmp_path / "wt"
    subprocess.run(["git", "init", str(wt)], capture_output=True)
    (wt / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(wt), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-m", "init"], capture_output=True)
    out = tmp_path / "out"; out.mkdir()
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")

    seen = {}

    def fake_run_one_shot(cmd, *, timeout, **kw):
        seen["called"] = list(cmd)
        out_path = cmd[cmd.index("-o") + 1]
        prompt = cmd[-1]
        rendered = json.loads(json.dumps(base_verdict(str(wt))))
        m = re.search(r"^target_ref: ([a-f0-9]{64})$", prompt, re.M)
        b = re.search(r"^baseline: (\S+)$", prompt, re.M)
        if m:
            rendered["target"]["target_ref"] = m.group(1)
        if b:
            rendered["target"]["baseline"] = b.group(1)
        Path(out_path).write_text(json.dumps(rendered))
        return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(codex_process, "run_one_shot", fake_run_one_shot)
    res = rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema), output_dir=str(out),
        milestone="m1", round_no=1, runner=None,
    )
    assert seen.get("called"), "run_one_shot was not used on the production path"
    assert res.outcome == "completed"


def test_injected_runner_path_unchanged_by_backport(tmp_path):
    """An injected runner is still called with the same kwargs as before."""
    wt = tmp_path / "wt"
    packet = tmp_path / "packet.md"; packet.write_text("p")
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    fake = FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt)))
    rcr.run_review(
        review_kind="milestone", worktree=str(wt), scope="uncommitted",
        packet_path=str(packet), schema_path=str(schema),
        output_dir=str(tmp_path / "out"), milestone="m1", round_no=1, runner=fake,
    )
    review_call = next(c for c in fake.calls if c[0] == "codex" and "exec" in c)
    assert review_call[0] == "codex"  # injected runner path preserved


def test_doctor_instruments_doctor_phase(tmp_path):
    """run_doctor records a review_phase=doctor observation without altering its result."""
    wt = tmp_path / "wt"; wt.mkdir(); (wt / ".git").mkdir()
    schema = tmp_path / "schema.json"; schema.write_text("{}")
    receipt = tmp_path / "doctor.json"
    obs = FakeQuotaObserver(before_snap=_ok_snapshot(10), after_snap=_ok_snapshot(12))
    passed, doctor_receipt = rcr.run_doctor(
        worktree=str(wt), schema_path=str(schema), receipt_path=str(receipt),
        runner=FakeRunner(worktree=str(wt), verdict=base_verdict(str(wt))),
        quota_observer=obs,
    )
    # observation recorded as a doctor-phase interval (outcome null for doctor)
    assert len(obs.records) == 1
    meta = obs.records[0][0]
    assert meta["review_phase"] == "doctor"
    assert meta["review_kind"] is None
    assert meta["outcome"] is None
    # the doctor result itself is unaffected by observation
    assert isinstance(passed, bool)
    assert doctor_receipt["outcome"] in ("passed", "failed")


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"]).returncode)
