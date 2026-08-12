#!/usr/bin/env python3
"""Unit and contract tests for run_codex_review.py.

No real Codex CLI is invoked. Subprocess execution is injected via a fake
runner that also serves the git calls, so the suite is hermetic and free.
Run: `pytest scripts/test_run_codex_review.py` from the skill root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_codex_review as rcr  # noqa: E402


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
            Path(out_path).write_text(
                json.dumps(self.verdict), encoding="utf-8"
            )
        return FakeCP(returncode=self.review_returncode, stdout="review done\n", stderr="")


def base_verdict(worktree: str, scope: str = "uncommitted", baseline: str = "deadbeefcafebabe000000000000000000000000", *, verdict: str = "approve", findings=None) -> dict:
    return {
        "verdict": verdict,
        "summary": "Looks good.",
        "target": {
            "repository": worktree,
            "worktree": worktree,
            "scope": scope,
            "baseline": baseline,
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
        "evidence": "ev",
        "affected_behavior": "ab",
        "recommendation": "r",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# build_command / scope_flags
# ---------------------------------------------------------------------------

def test_build_command_flag_placement():
    cmd = rcr.build_command(
        worktree="/tmp/wt", scope="uncommitted",
        schema_path="/tmp/s.json", out_path="/tmp/out.json",
    )
    # exec-level flags BEFORE the review subcommand
    assert cmd[:6] == ["codex", "exec", "-C", "/tmp/wt", "-s", "read-only"]
    assert "review" in cmd
    review_idx = cmd.index("review")
    exec_seg = cmd[:review_idx]
    assert "-C" in exec_seg and "-s" in exec_seg
    # review-level flags AFTER the subcommand
    after = cmd[review_idx:]
    assert "--uncommitted" in after
    assert "--output-schema" in after and "-o" in after
    # determinism levers default to the review level (review --help documents them)
    assert "--ephemeral" in after and "--ignore-rules" in after
    assert "--ephemeral" not in exec_seg
    # command is an argument vector, not a shell string
    assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)


def test_build_command_scope_flags():
    assert rcr.scope_flags(scope="uncommitted", base=None, commit=None) == ["--uncommitted"]
    assert rcr.scope_flags(scope="base", base="main", commit=None) == ["--base", "main"]
    assert rcr.scope_flags(scope="commit", base=None, commit="abc123") == ["--commit", "abc123"]


def test_build_command_model_optional():
    without = rcr.build_command(worktree="/w", scope="uncommitted", schema_path="/s", out_path="/o")
    with_model = rcr.build_command(worktree="/w", scope="uncommitted", schema_path="/s", out_path="/o", model="gpt-5")
    assert "-m" not in without
    assert with_model[with_model.index("-m") + 1] == "gpt-5"


def test_scope_exclusivity():
    import pytest
    with pytest.raises(ValueError):
        rcr.scope_flags(scope="uncommitted", base="main", commit=None)
    with pytest.raises(ValueError):
        rcr.scope_flags(scope="base", base=None, commit=None)
    with pytest.raises(ValueError):
        rcr.scope_flags(scope="commit", base="main", commit="abc")
    with pytest.raises(ValueError):
        rcr.scope_flags(scope="bogus", base=None, commit=None)


# ---------------------------------------------------------------------------
# validate_inputs
# ---------------------------------------------------------------------------

def _valid_inputs(**over):
    base = dict(
        worktree="/tmp/wt", scope="uncommitted", base=None, commit=None,
        packet_path="/tmp/packet.md", schema_path="/tmp/s.json", output_dir="/tmp/out",
    )
    base.update(over)
    return base


def test_validate_inputs_ok(tmp_path):
    packet = tmp_path / "p.md"; packet.write_text("x")
    schema = tmp_path / "s.json"; schema.write_text("{}")
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
    schema = tmp_path / "s.json"; schema.write_text("{}")
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
    assert rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"]) == []


def test_validate_verdict_needs_attention_with_finding():
    v = base_verdict("/tmp/wt", verdict="needs-attention", findings=[finding()])
    assert rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"]) == []


def test_validate_verdict_approve_with_blocking_rejected():
    v = base_verdict("/tmp/wt", findings=[finding(severity="high")])
    errs = rcr.validate_verdict(v, expected_worktree="/tmp/wt", expected_scope="uncommitted", expected_baseline=v["target"]["baseline"])
    assert any("approve but a blocking" in e for e in errs)


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
         base=None, commit=None, state_ledger=None):
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
        worktree=str(wt), scope=scope, base=base, commit=commit,
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
    assert "blocking" in res.diagnostics


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
    assert not any(call[0] == "codex" and "review" in call for call in fake.calls)


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


def test_run_review_scope_base_uses_baseline(tmp_path):
    wt = tmp_path / "wt"
    v = base_verdict(str(wt), scope="base", baseline="main")
    res, wta, baseline, fake, out = _run(tmp_path, verdict=v, scope="base", base="main")
    assert res.outcome == "completed"
    # the --base main flag is present in the recorded command
    receipt = json.loads(next(Path(out).glob("receipt-*.json")).read_text())
    assert "--base" in receipt["transport"]["command"]
    assert "main" in receipt["transport"]["command"]


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


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"]).returncode)
