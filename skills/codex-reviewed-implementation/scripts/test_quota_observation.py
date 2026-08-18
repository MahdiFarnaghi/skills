#!/usr/bin/env python3
"""Tests for quota_observation.py — normalization, delta, JSONL logging.

Run: `python3.11 -m pytest scripts/test_quota_observation.py -q` from the skill root.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quota_observation as qo  # noqa: E402


# ---------------------------------------------------------------------------
# salt + account pseudonym
# ---------------------------------------------------------------------------

def test_ensure_salt_creates_and_reuses(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    salt1 = qo.ensure_salt(str(log))
    salt2 = qo.ensure_salt(str(log))
    assert isinstance(salt1, bytes) and len(salt1) == 32
    assert salt1 == salt2  # reused, not regenerated
    salt_path = tmp_path / qo.SALT_FILENAME
    assert salt_path.exists()
    mode = stat.S_IMODE(salt_path.stat().st_mode)
    assert mode == 0o600


def test_ensure_salt_distinct_per_store(tmp_path):
    a = qo.ensure_salt(str(tmp_path / "a" / "quota.jsonl"))
    b = qo.ensure_salt(str(tmp_path / "b" / "quota.jsonl"))
    assert a != b


def test_account_pseudonym_stable_and_private():
    salt = bytes.fromhex("00" * 32)
    p1 = qo.account_pseudonym("alice@example.com", salt)
    p2 = qo.account_pseudonym("alice@example.com", salt)
    assert p1 == p2
    assert len(p1) == 16
    # The raw email never appears in the pseudonym.
    assert "alice" not in p1 and "example" not in p1


def test_account_pseudonym_none_inputs():
    salt = bytes.fromhex("00" * 32)
    assert qo.account_pseudonym(None, salt) is None
    assert qo.account_pseudonym("", salt) is None
    assert qo.account_pseudonym("alice@example.com", None) is None


def test_account_pseudonym_differs_for_different_accounts():
    salt = bytes.fromhex("00" * 32)
    assert qo.account_pseudonym("a@x.com", salt) != qo.account_pseudonym("b@x.com", salt)


# ---------------------------------------------------------------------------
# normalize_candidates
# ---------------------------------------------------------------------------

SALT = bytes.fromhex("01" * 32)


def _window(used, *, resets=1000, mins=10080):
    return {"usedPercent": used, "resetsAt": resets, "windowDurationMins": mins}


def test_normalize_single_bucket_primary():
    resp = {"rateLimits": {"limitId": "codex", "primary": _window(42)}}
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ok"
    assert len(cands) == 1
    c = cands[0]
    assert c["window_role"] == "primary"
    assert c["used_percent"] == 42
    assert c["window_duration_mins"] == 10080
    assert c["resets_at"] == 1000
    assert len(c["candidate_id"]) == 16


def test_normalize_records_all_windows_never_picks_weekly():
    resp = {"rateLimits": {"limitId": "codex", "primary": _window(10, mins=1440),
                           "secondary": _window(55, mins=10080)}}
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ok"
    roles = sorted(c["window_role"] for c in cands)
    assert roles == ["primary", "secondary"]
    # both window durations are preserved verbatim; none is filtered as "weekly"
    assert {c["window_duration_mins"] for c in cands} == {1440, 10080}


def test_normalize_multi_bucket_map():
    resp = {"rateLimitsByLimitId": {
        "codex": {"primary": _window(20)},
        "other": {"primary": _window(80)},
    }}
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ok"
    assert len(cands) == 2
    ids = {c["candidate_id"] for c in cands}
    assert len(ids) == 2  # distinct candidates


def test_normalize_duplicate_identity_is_ambiguous():
    # Same limit reported in both the single bucket and the keyed map.
    resp = {
        "rateLimits": {"limitId": "codex", "primary": _window(20)},
        "rateLimitsByLimitId": {"codex": {"primary": _window(21)}},
    }
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ambiguous"


def test_normalize_missing_identity_is_ambiguous():
    # A window is present but the snapshot carries no stable identity at all.
    resp = {"rateLimits": {"primary": _window(20)}}
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ambiguous"


def test_normalize_skips_window_without_used_percent():
    resp = {"rateLimits": {"limitId": "codex", "primary": {"resetsAt": 1, "windowDurationMins": 10},
                           "secondary": _window(5)}}
    cands, status = qo.normalize_candidates(resp, SALT)
    assert status == "ok"
    assert [c["window_role"] for c in cands] == ["secondary"]


def test_normalize_candidate_id_stable_for_matching():
    resp = {"rateLimits": {"limitId": "codex", "primary": _window(20)}}
    a, _ = qo.normalize_candidates(resp, SALT)
    b, _ = qo.normalize_candidates(resp, SALT)
    assert a[0]["candidate_id"] == b[0]["candidate_id"]


def test_normalize_no_salt_yields_null_ids():
    resp = {"rateLimits": {"limitId": "codex", "primary": _window(20)}}
    cands, status = qo.normalize_candidates(resp, None)
    # Degraded: still records the metric, but candidate ids are null (no matching).
    assert status == "ok"
    assert cands[0]["candidate_id"] is None
    assert cands[0]["used_percent"] == 20


# ---------------------------------------------------------------------------
# candidate_delta null rules
# ---------------------------------------------------------------------------

def _c(used, *, resets=1000, mins=10080, cid="C1", role="primary"):
    return {"candidate_id": cid, "window_role": role, "window_duration_mins": mins,
            "resets_at": resets, "used_percent": used}


def test_delta_same_window_increase():
    assert qo.candidate_delta(_c(42), _c(48)) == 6


def test_delta_null_when_counter_decreased():
    assert qo.candidate_delta(_c(48), _c(42)) is None


def test_delta_null_when_window_duration_changed():
    assert qo.candidate_delta(_c(42, mins=1440), _c(48, mins=10080)) is None


def test_delta_null_when_window_reset():
    # resets_at moved forward -> the window rolled over during the interval.
    assert qo.candidate_delta(_c(42, resets=1000), _c(48, resets=2000)) is None


def test_delta_null_when_resets_at_only_one_side():
    assert qo.candidate_delta(_c(42, resets=None), _c(48, resets=2000)) is None


def test_delta_allows_both_resets_null():
    assert qo.candidate_delta(_c(42, resets=None), _c(48, resets=None)) == 6


def test_delta_zero_is_valid():
    assert qo.candidate_delta(_c(42), _c(42)) == 0


# ---------------------------------------------------------------------------
# merge_candidate_limits
# ---------------------------------------------------------------------------

def test_merge_pairs_by_candidate_id():
    before = [_c(40, cid="A"), _c(10, cid="B")]
    after = [_c(50, cid="A"), _c(15, cid="B")]
    merged = {m["candidate_id"]: m for m in qo.merge_candidate_limits(before, after, accounts_match=True)}
    assert merged["A"]["observed_account_delta_during_interval"] == 10
    assert merged["B"]["observed_account_delta_during_interval"] == 5
    assert merged["A"]["used_percent_before"] == 40
    assert merged["A"]["used_percent_after"] == 50


def test_merge_accounts_differ_forces_null_delta():
    before = [_c(40, cid="A")]
    after = [_c(50, cid="A")]
    merged = qo.merge_candidate_limits(before, after, accounts_match=False)
    assert merged[0]["observed_account_delta_during_interval"] is None


def test_merge_candidate_only_on_one_side():
    before = [_c(40, cid="A")]
    after = []
    merged = {m["candidate_id"]: m for m in qo.merge_candidate_limits(before, after, accounts_match=True)}
    assert merged["A"]["used_percent_before"] == 40
    assert merged["A"]["used_percent_after"] is None
    assert merged["A"]["observed_account_delta_during_interval"] is None


def test_merge_unmatchable_null_id_candidates_recorded():
    before = [{"candidate_id": None, "window_role": "primary", "window_duration_mins": 10080,
               "resets_at": 1, "used_percent": 30}]
    merged = qo.merge_candidate_limits(before, [], accounts_match=True)
    assert len(merged) == 1
    assert merged[0]["candidate_id"] is None
    assert merged[0]["observed_account_delta_during_interval"] is None


# ---------------------------------------------------------------------------
# append_observation (atomic, locked, 0600, NDJSON)
# ---------------------------------------------------------------------------

def test_append_creates_log_mode_0600(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    qo.append_observation(str(log), {"invocation_id": "i1", "v": 1})
    assert log.exists()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["invocation_id"] == "i1"


def test_append_one_line_per_record(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    for i in range(3):
        qo.append_observation(str(log), {"invocation_id": f"i{i}"})
    lines = log.read_text().splitlines()
    assert len(lines) == 3
    assert [json.loads(l)["invocation_id"] for l in lines] == ["i0", "i1", "i2"]


def test_append_concurrent_no_lost_or_interleaved_records(tmp_path):
    """Parallel writers must not corrupt or interleave JSONL records."""
    import multiprocessing as mp

    log = tmp_path / "quota-observations.jsonl"

    procs = [mp.Process(target=_concurrent_worker, args=(str(log), n)) for n in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    lines = log.read_text().splitlines()
    assert len(lines) == 100  # 4 * 25, none lost
    # every line is independently valid JSON
    for line in lines:
        json.loads(line)


def _concurrent_worker(log: str, n: int) -> None:
    for i in range(25):
        qo.append_observation(log, {"worker": n, "i": i})


# ---------------------------------------------------------------------------
# rotation + retention
# ---------------------------------------------------------------------------

def test_rotation_at_threshold(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    # Pre-fill the current log past the threshold.
    log.write_text("OLDLINE\n" * 60)  # > 300 bytes
    qo.append_observation(str(log), {"new": 1}, max_bytes=300)
    rotated = list(tmp_path.glob("quota-observations.*.jsonl"))
    assert len(rotated) == 1
    assert "OLDLINE" in rotated[0].read_text()
    # current log now holds only the record appended after rotation
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["new"] == 1


def test_rotation_disabled_when_max_bytes_zero(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    qo.append_observation(str(log), {"x": "A" * 500}, max_bytes=0)
    assert not list(tmp_path.glob("quota-observations.*.jsonl"))


def test_retention_keeps_floor_and_drops_old(tmp_path):
    log = tmp_path / "quota-observations.jsonl"
    now = 1_700_000_000.0
    import os as _os
    # 8 rotated files, each with a distinct monotonically older mtime
    # (file 0 oldest at 200d, file 7 newest at 25d). Files 0-4 exceed 90 days.
    for i in range(8):
        p = tmp_path / f"quota-observations.{i}.jsonl"
        p.write_text("[]")
        age_days = (8 - i) * 25
        _os.utime(p, (now - age_days * 86400, now - age_days * 86400))
    qo.append_observation(str(log), {"x": 1}, max_bytes=10**9, retain_days=90, min_files=5,
                          now_epoch=lambda: now)
    survivors = {
        int(p.name.split(".")[1])
        for p in tmp_path.glob("quota-observations.[0-9]*.jsonl")
    }
    # The three oldest (>90d and beyond the 5-newest floor) are dropped. The floor
    # keeps the five newest by mtime even though file 4 (100d) is also >90d.
    assert survivors == {3, 4, 5, 6, 7}
    assert {0, 1, 2}.isdisjoint(survivors)


# ---------------------------------------------------------------------------
# build_observation_record
# ---------------------------------------------------------------------------

def _snapshot(status, acct, cands, diag=""):
    return {"status": status, "account_pseudonym": acct, "candidates": cands, "diagnostic": diag}


def test_build_record_merges_snapshots_with_delta():
    before = _snapshot("ok", "acct1", [_c(40, cid="A")])
    after = _snapshot("ok", "acct1", [_c(50, cid="A")])
    meta = {"invocation_id": "inv1", "review_phase": "milestone", "outcome": "completed",
            "verdict": "approve"}
    rec = qo.build_observation_record(meta, before, after)
    assert rec["schema_version"] == qo.OBSERVATION_SCHEMA_VERSION
    assert rec["invocation_id"] == "inv1"
    assert rec["account_pseudonym"] == "acct1"
    assert rec["snapshot_status_before"] == "ok"
    assert rec["snapshot_status_after"] == "ok"
    cl = rec["candidate_limits"]
    assert cl[0]["observed_account_delta_during_interval"] == 10


def test_build_record_accounts_differ_nulls_delta():
    before = _snapshot("ok", "acct1", [_c(40, cid="A")])
    after = _snapshot("ok", "acct2", [_c(50, cid="A")])
    rec = qo.build_observation_record({}, before, after)
    assert rec["candidate_limits"][0]["observed_account_delta_during_interval"] is None


def test_build_record_missing_after_snapshot():
    before = _snapshot("ok", "acct1", [_c(40, cid="A")])
    rec = qo.build_observation_record({"review_phase": "milestone"}, before, None)
    assert rec["snapshot_status_after"] == "none"
    assert rec["candidate_limits"][0]["used_percent_before"] == 40
    assert rec["candidate_limits"][0]["used_percent_after"] is None


def test_record_conforms_to_snapshot_schema():
    """The emitted record carries every field the snapshot schema requires."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "schemas", "codex-quota-snapshot.schema.json"
    )
    schema = json.loads(Path(schema_path).read_text())
    before = _snapshot("ok", "acct1", [_c(40, cid="A")])
    after = _snapshot("ok", "acct1", [_c(50, cid="A")])
    rec = qo.build_observation_record(
        {"invocation_id": "i1", "review_phase": "milestone", "outcome": "completed"}, before, after
    )
    for key in schema["required"]:
        assert key in rec, f"record missing required field {key!r}"
    cand_schema = schema["properties"]["candidate_limits"]["items"]
    for key in cand_schema["required"]:
        assert key in rec["candidate_limits"][0], f"candidate missing {key!r}"


def test_profile_fields_survive_meta_allowlist():
    """Phase 7: the execution profile must reach the JSONL store. META_FIELDS
    is a strict allowlist that silently drops unknown keys — wrapper-side
    tests alone would pass while the store loses the data."""
    before = _snapshot("ok", "acct1", [])
    rec = qo.build_observation_record({
        "invocation_id": "i1", "review_phase": "milestone", "outcome": "completed",
        "model": "gpt-5.6-luna", "reasoning_effort": "medium",
        "config_version": 1, "resolution_source": "project_config",
        "profile_digest": "d" * 64,
    }, before, before)
    for key in ("model", "reasoning_effort", "config_version",
                "resolution_source", "profile_digest"):
        assert rec.get(key) is not None, f"{key} dropped by META_FIELDS allowlist"


def test_snapshot_schema_declares_profile_fields():
    """additionalProperties:false means an undeclared field would make every
    new record schema-invalid."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "schemas", "codex-quota-snapshot.schema.json"
    )
    schema = json.loads(Path(schema_path).read_text())
    for key in ("reasoning_effort", "config_version", "resolution_source",
                "profile_digest"):
        assert key in schema["properties"], f"snapshot schema missing {key!r}"


def test_record_persists_no_raw_identity_or_plan():
    """Raw account identifiers, plan, and limit labels never reach the record."""
    salt = bytes.fromhex("07" * 32)
    resp = {"rateLimits": {"limitId": "codex-plus-weekly", "planType": "plus",
                           "primary": {"usedPercent": 42, "resetsAt": 1000, "windowDurationMins": 10080}}}
    cands, _ = qo.normalize_candidates(resp, salt)
    pseudo = qo.account_pseudonym("alice@example.com", salt)
    before = _snapshot("ok", pseudo, cands)
    rec = qo.build_observation_record(
        {"invocation_id": "i1", "review_phase": "milestone", "outcome": "completed"}, before, before
    )
    blob = json.dumps(rec)
    for forbidden in ("alice@example.com", "alice", "example.com", "planType", "plus",
                      "codex-plus-weekly", "limitId", "limitName", "email"):
        assert forbidden not in blob, f"raw identity/label {forbidden!r} leaked into record"
    assert rec["account_pseudonym"] == pseudo
    assert rec["candidate_limits"][0]["candidate_id"] != "codex-plus-weekly"
