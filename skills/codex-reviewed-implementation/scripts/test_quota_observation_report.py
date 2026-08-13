#!/usr/bin/env python3
"""Tests for summarize_quota_observations.py — eligibility + report.

Run: `python3.11 -m pytest scripts/test_quota_observation_report.py -q` from skill root.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import summarize_quota_observations as sq  # noqa: E402

T0 = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ts(days: float) -> str:
    return (T0 + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(*, phase="milestone", kind="milestone", day=0.0, status_before="ok",
          status_after="ok", candidates=None, model="gpt-5.4", outcome="completed",
          invocation_id="inv"):
    return {
        "schema_version": 1,
        "invocation_id": f"{invocation_id}-{day}",
        "review_kind": kind,
        "review_phase": phase,
        "model": model,
        "outcome": outcome,
        "started_at": _ts(day),
        "snapshot_status_before": status_before,
        "snapshot_status_after": status_after,
        "candidate_limits": candidates or [],
    }


def _cand(cid, used_before, used_after, *, mins=10080, delta=None):
    return {"candidate_id": cid, "window_role": "primary", "window_duration_mins": mins,
            "used_percent_before": used_before, "used_percent_after": used_after,
            "resets_at_before": 1000, "resets_at_after": 1000,
            "observed_account_delta_during_interval": delta}


# ---------------------------------------------------------------------------
# check_eligibility
# ---------------------------------------------------------------------------

def test_eligibility_met():
    recs = []
    for i in range(15):
        recs.append(_rec(kind="milestone", day=i * 2))
    for i in range(15):
        recs.append(_rec(phase="plan_challenge", kind="plan", day=i * 2))
    res = sq.check_eligibility(recs)
    assert res["eligible"] is True
    assert res["interval_count"] == 30
    assert res["days_spanned"] >= 28
    assert {"plan", "milestone"} <= set(res["review_kinds"])


def test_eligibility_short_span():
    # 30 intervals but compressed into < 28 days.
    recs = [_rec(day=d % 20) for d in range(30)]  # span ~19 days
    res = sq.check_eligibility(recs)
    assert res["eligible"] is False
    assert any("28" in u or "day" in u.lower() for u in res["unmet"])


def test_eligibility_too_few_intervals():
    recs = [_rec(kind="milestone", day=d) for d in range(0, 40, 4)]  # 10 intervals
    res = sq.check_eligibility(recs)
    assert res["eligible"] is False
    assert any("30" in u or "interval" in u.lower() for u in res["unmet"])


def test_eligibility_missing_review_kind():
    recs = [_rec(kind="milestone", day=d) for d in range(0, 60, 1)][:30]
    res = sq.check_eligibility(recs)
    assert res["eligible"] is False
    assert any("kind" in u.lower() or "coverage" in u.lower() for u in res["unmet"])


def test_eligibility_empty():
    res = sq.check_eligibility([])
    assert res["eligible"] is False


# ---------------------------------------------------------------------------
# recommendation rule
# ---------------------------------------------------------------------------

def test_recommendation_continue_when_not_eligible():
    res = sq.recommendation([_rec(candidates=[_cand("C1", 80, 80)])], eligible=False)
    assert res["recommendation"] == "continue_observation"


def test_recommendation_consider_advisory_three_high_intervals():
    recs = [_rec(candidates=[_cand("C1", 70 + i, 70 + i)], invocation_id=f"r{i}") for i in range(3)]
    res = sq.recommendation(recs, eligible=True)
    assert res["recommendation"] == "consider_advisory"
    c1 = next(c for c in res["per_candidate"] if c["candidate_id"] == "C1")
    assert c1["intervals_at_high"] == 3
    assert c1["triggered_advisory"] is True


def test_recommendation_consider_advisory_low_fraction():
    # 5 valid intervals, 1 at >=60 (20%) -> advisory.
    recs = [
        _rec(candidates=[_cand("C1", 60, 60)], invocation_id="hi"),
        *[_rec(candidates=[_cand("C1", 10, 10)], invocation_id=f"lo{i}") for i in range(4)],
    ]
    res = sq.recommendation(recs, eligible=True)
    assert res["recommendation"] == "consider_advisory"
    c1 = next(c for c in res["per_candidate"] if c["candidate_id"] == "C1")
    assert c1["valid_intervals"] == 5
    assert c1["intervals_at_low"] == 1
    assert c1["low_fraction"] >= 0.20


def test_recommendation_no_action_when_quiet():
    recs = [_rec(candidates=[_cand("C1", 20, 25)], invocation_id=f"r{i}") for i in range(10)]
    res = sq.recommendation(recs, eligible=True)
    assert res["recommendation"] == "no_action_warranted"


def test_recommendation_interval_counted_once_per_threshold():
    # 6 intervals; in one of them both C1 and C2 cross 70 (and 60). Each candidate
    # must count that interval ONCE per threshold (not twice for before+after).
    recs = [
        _rec(candidates=[_cand("C1", 75, 75), _cand("C2", 80, 80)], invocation_id="cross"),
        *[_rec(candidates=[_cand("C1", 5, 5), _cand("C2", 5, 5)], invocation_id=f"lo{i}") for i in range(5)],
    ]
    res = sq.recommendation(recs, eligible=True)
    for c in res["per_candidate"]:
        assert c["valid_intervals"] == 6
        assert c["intervals_at_low"] == 1
        assert c["intervals_at_high"] == 1
    # 1/6 ~ 16.7% < 20% and 1 high < 3 -> no advisory
    assert res["recommendation"] == "no_action_warranted"


def test_recommendation_states_rule_and_thresholds():
    res = sq.recommendation([], eligible=True)
    assert res["thresholds"]["high"] == 70
    assert res["thresholds"]["low"] == 60
    assert "rule" in res and "consider_advisory" in res["rule"]


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

def _sample_records():
    return [
        _rec(kind="milestone", day=0, candidates=[_cand("C1", 55, 62)]),
        _rec(kind="milestone", day=1, candidates=[_cand("C1", 62, 71)]),
        _rec(phase="plan_challenge", kind="plan", day=2, candidates=[_cand("C1", 10, 12)]),
    ]


def test_build_report_shape_and_status():
    rep = sq.build_report(_sample_records())
    assert rep["schema_version"] == sq.REPORT_SCHEMA_VERSION
    assert rep["status"] == "decision_required"
    assert "recommendation" in rep
    assert "eligibility" in rep
    assert rep["input_record_count"] == 3
    assert "records_digest" in rep and len(rep["records_digest"]) == 16


def test_build_report_digest_is_deterministic_and_order_independent():
    recs = _sample_records()
    d1 = sq.build_report(recs)["records_digest"]
    d2 = sq.build_report(list(reversed(recs)))["records_digest"]
    assert d1 == d2
    other = sq.build_report([_rec(kind="milestone", day=9, candidates=[_cand("C1", 1, 1)])])["records_digest"]
    assert other != d1


def test_build_report_used_percent_counts():
    rep = sq.build_report(_sample_records())
    up = rep["used_percent"]
    # peaks: 62, 71, 12 -> two at >=60, one at >=70
    assert up["count_ge_60"] == 2
    assert up["count_ge_70"] == 1
    assert up["max"] == 71


def test_build_report_coverage_and_corrupt():
    rep = sq.build_report(_sample_records(), corrupt_record_count=2)
    cov = rep["coverage"]
    assert cov["by_review_kind"] == {"milestone": 2, "plan": 1}
    assert rep["snapshot_status_counts"]["corrupt"] == 2


def test_build_report_delta_null_proportion():
    # C1 before/after same window -> delta computed; add one unmatched candidate.
    recs = [_rec(candidates=[_cand("C1", 50, 55, delta=5)])]
    rep = sq.build_report(recs)
    dc = rep["delta_correlation"]
    assert dc["total"] >= 1
    assert dc["non_null"] >= 1
    assert 0.0 <= dc["null_fraction"] <= 1.0


def test_build_report_labels_delta_as_correlation_not_attribution():
    rep = sq.build_report(_sample_records())
    assert rep["delta_correlation"]["interpretation"] == "interval_correlation_not_attributed_usage"


# ---------------------------------------------------------------------------
# write_report + summarize (top-level: read log, count corrupt, write idempotently)
# ---------------------------------------------------------------------------

def _eligible_records():
    recs = []
    for i in range(15):
        recs.append(_rec(kind="milestone", day=i * 2, candidates=[_cand("C1", 30 + i, 32 + i)],
                         invocation_id=f"m{i}"))
    for i in range(15):
        recs.append(_rec(phase="plan_challenge", kind="plan", day=i * 2,
                         candidates=[_cand("C1", 10, 11)], invocation_id=f"p{i}"))
    return recs


def _write_log(path, recs, extra_corrupt=0):
    import quota_observation as qo
    for r in recs:
        qo.append_observation(str(path), r, max_bytes=0)
    with open(path, "a") as fh:
        for _ in range(extra_corrupt):
            fh.write("{ not valid json\n")


def test_summarize_writes_report_when_eligible(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, _eligible_records())
    res = sq.summarize(str(log))
    assert res["status"] == "decision_required"
    assert res["written"] is True
    report_json = tmp_path / "quota-report.json"
    report_md = tmp_path / "quota-report.md"
    assert report_json.exists() and report_md.exists()
    import stat
    assert stat.S_IMODE(report_json.stat().st_mode) == 0o600
    rep = json.loads(report_json.read_text())
    assert rep["status"] == "decision_required"
    assert rep["input_record_count"] == 30


def test_summarize_idempotent_same_digest(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, _eligible_records())
    first = sq.summarize(str(log))
    second = sq.summarize(str(log))
    assert first["written"] is True
    assert second["written"] is False  # unchanged digest -> no-op


def test_summarize_not_eligible_no_file(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, [_rec(day=0), _rec(day=1)])  # tiny sample
    res = sq.summarize(str(log))
    assert res["status"] == "continue_observation"
    assert res["written"] is False
    assert not (tmp_path / "quota-report.json").exists()


def test_summarize_counts_and_skips_corrupt(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, _eligible_records(), extra_corrupt=3)
    res = sq.summarize(str(log))
    rep = json.loads((tmp_path / "quota-report.json").read_text())
    assert rep["corrupt_record_count"] == 3
    assert rep["input_record_count"] == 30  # corrupt records skipped, not counted as input


def test_summarize_includes_retained_rotated_logs(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    records = _eligible_records()
    rotated = tmp_path / "quota-observations.100.jsonl"
    _write_log(rotated, records[:20])
    _write_log(log, records[20:])
    res = sq.summarize(str(log))
    assert res["status"] == "decision_required"
    report = json.loads((tmp_path / "quota-report.json").read_text())
    assert report["input_record_count"] == 30


def test_summarize_replaces_when_data_changes(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, _eligible_records())
    sq.summarize(str(log))
    # add more records -> digest changes -> report regenerated
    import quota_observation as qo
    qo.append_observation(str(log), _rec(kind="milestone", day=40,
                          candidates=[_cand("C1", 90, 92)], invocation_id="extra"), max_bytes=0)
    res = sq.summarize(str(log))
    assert res["written"] is True


def test_report_conforms_to_report_schema(tmp_path):
    """The emitted report carries every field the report schema requires."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "schemas", "codex-quota-report.schema.json"
    )
    schema = json.loads(Path(schema_path).read_text())
    log = tmp_path / "quota-observations.jsonl"
    _write_log(log, _eligible_records())
    sq.summarize(str(log))
    report = json.loads((tmp_path / "quota-report.json").read_text())
    for key in schema["required"]:
        assert key in report, f"report missing required field {key!r}"
    assert report["status"] == "decision_required"
    assert report["recommendation"] in ("no_action_warranted", "consider_advisory")
