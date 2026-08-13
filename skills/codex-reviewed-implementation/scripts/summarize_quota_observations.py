#!/usr/bin/env python3
"""Quota observation report: eligibility check + deterministic report (Phase 6).

Reads the normalized JSONL observation records produced by ``quota_observation``
and, once the observation window is satisfied, produces ONE idempotent,
non-authoritative evidence report. The report is **decision evidence, never
policy**: it may recommend ``no_action_warranted`` / ``continue_observation`` /
``consider_advisory``, but it can never enable a warning, change thresholds, or
alter review behavior without a separate user decision and implementation change.

All logic here is pure over the record stream so it is fully unit-testable
without a live Codex call.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Constants (defaults; rule changes create a new report-policy version)
# ---------------------------------------------------------------------------

REPORT_SCHEMA_VERSION = 1
MIN_DAYS = 28
MIN_INTERVALS = 30
REQUIRED_REVIEW_KINDS = ("plan", "milestone")

# Recommendation thresholds (plan "Evaluation criteria").
THRESHOLD_HIGH = 70   # consider_advisory if >=3 distinct intervals reach this
THRESHOLD_LOW = 60    #   ...or >=20% of valid intervals reach this
HIGH_INTERVAL_MIN = 3
LOW_FRACTION = 0.20


def _parse_ts(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_interval(rec: dict) -> bool:
    """A record counts as one observed interval if a before-snapshot was attempted."""
    return rec.get("snapshot_status_before") not in (None, "none", "")


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def check_eligibility(records: Iterable[dict]) -> dict:
    """Decide whether the observation window is satisfied (plan "Outcome").

    Eligible once: the observed time span is >= 28 days AND at least 30 observed
    intervals AND both ``plan`` and ``milestone`` review kinds are represented.
    Returns a dict with ``eligible``, ``days_spanned``, ``interval_count``,
    ``review_kinds``, and ``unmet`` (human-readable conditions still outstanding).
    """
    records = list(records)
    timestamps: list[datetime] = []
    intervals = 0
    kinds: set[str] = set()
    for rec in records:
        ts = _parse_ts(rec.get("started_at"))
        if ts is not None:
            timestamps.append(ts)
        if _is_interval(rec):
            intervals += 1
            rk = rec.get("review_kind")
            if isinstance(rk, str):
                kinds.add(rk)

    days_spanned = 0.0
    if len(timestamps) >= 2:
        days_spanned = (max(timestamps) - min(timestamps)).total_seconds() / 86400.0

    unmet: list[str] = []
    if days_spanned < MIN_DAYS:
        unmet.append(f"observed span {days_spanned:.1f} days < {MIN_DAYS} days")
    if intervals < MIN_INTERVALS:
        unmet.append(f"observed intervals {intervals} < {MIN_INTERVALS}")
    missing_kinds = sorted(set(REQUIRED_REVIEW_KINDS) - kinds)
    if missing_kinds:
        unmet.append(f"missing review-kind coverage: {missing_kinds}")

    return {
        "eligible": not unmet,
        "days_spanned": round(days_spanned, 2),
        "interval_count": intervals,
        "review_kinds": sorted(kinds),
        "unmet": unmet,
    }


# ---------------------------------------------------------------------------
# Signal evaluation + recommendation
# ---------------------------------------------------------------------------

RECOMMENDATION_RULE = (
    f"consider_advisory when, for any one stable candidate identity, at least "
    f"{HIGH_INTERVAL_MIN} distinct completed intervals reach usedPercent >= "
    f"{THRESHOLD_HIGH}, or at least {int(LOW_FRACTION * 100)}% of intervals in "
    f"which that candidate is valid reach usedPercent >= {THRESHOLD_LOW}; "
    f"continue_observation before eligibility; no_action_warranted otherwise."
)


def _peak_used(cl: dict) -> int | None:
    """Max observed used_percent across the before/after window, or None."""
    obs = [v for v in (cl.get("used_percent_before"), cl.get("used_percent_after"))
           if isinstance(v, int)]
    return max(obs) if obs else None


def evaluate_signal(records: Iterable[dict]) -> dict:
    """Aggregate per-candidate interval sets at the low/high thresholds.

    Each interval (record, keyed by ``invocation_id``) is counted at most once per
    threshold for a given candidate, even if both before and after cross it.
    """
    by_cand: dict[str, dict] = {}
    for rec in records:
        iid = rec.get("invocation_id")
        if not iid:
            continue
        for cl in rec.get("candidate_limits") or []:
            cid = cl.get("candidate_id")
            if not cid:
                continue
            peak = _peak_used(cl)
            if peak is None:
                continue
            entry = by_cand.setdefault(cid, {"valid": set(), "low": set(), "high": set()})
            entry["valid"].add(iid)
            if peak >= THRESHOLD_LOW:
                entry["low"].add(iid)
            if peak >= THRESHOLD_HIGH:
                entry["high"].add(iid)
    return by_cand


def recommendation(records: Iterable[dict], *, eligible: bool) -> dict:
    """Produce exactly one deterministic recommendation from the record stream."""
    if not eligible:
        return {
            "recommendation": "continue_observation",
            "rule": RECOMMENDATION_RULE,
            "rule_version": REPORT_SCHEMA_VERSION,
            "thresholds": {"high": THRESHOLD_HIGH, "low": THRESHOLD_LOW},
            "per_candidate": [],
        }

    by_cand = evaluate_signal(records)
    per_candidate = []
    triggered = False
    for cid in sorted(by_cand):
        sets = by_cand[cid]
        valid = len(sets["valid"])
        high_n = len(sets["high"])
        low_n = len(sets["low"])
        frac = (low_n / valid) if valid else 0.0
        trig = (high_n >= HIGH_INTERVAL_MIN) or (frac >= LOW_FRACTION)
        triggered = triggered or trig
        per_candidate.append({
            "candidate_id": cid,
            "valid_intervals": valid,
            "intervals_at_high": high_n,
            "intervals_at_low": low_n,
            "low_fraction": round(frac, 4),
            "triggered_advisory": trig,
            "supporting_interval_ids": sorted(sets["high"] | sets["low"])[:50],
        })

    return {
        "recommendation": "consider_advisory" if triggered else "no_action_warranted",
        "rule": RECOMMENDATION_RULE,
        "rule_version": REPORT_SCHEMA_VERSION,
        "thresholds": {"high": THRESHOLD_HIGH, "low": THRESHOLD_LOW},
        "per_candidate": per_candidate,
    }


# ---------------------------------------------------------------------------
# Aggregates + full report
# ---------------------------------------------------------------------------

SNAPSHOT_STATUSES = ("ok", "unavailable", "incompatible", "ambiguous", "error", "none", "corrupt")


def _records_digest(records: list[dict]) -> str:
    """Stable digest of the processed record set (order-independent)."""
    payload = "\n".join(
        json.dumps(rec, sort_keys=True, separators=(",", ":"))
        for rec in sorted(records, key=lambda r: str(r.get("invocation_id", "")))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _coverage(records: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    by_model: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    days: set[str] = set()
    for rec in records:
        for src, bucket in (
            (rec.get("review_kind"), by_kind),
            (rec.get("review_phase"), by_phase),
            (rec.get("model"), by_model),
            (rec.get("outcome"), by_outcome),
        ):
            if isinstance(src, str):
                bucket[src] = bucket.get(src, 0) + 1
        ts = _parse_ts(rec.get("started_at"))
        if ts is not None:
            days.add(ts.date().isoformat())
    return {
        "by_review_kind": by_kind,
        "by_review_phase": by_phase,
        "by_model": by_model,
        "by_outcome": by_outcome,
        "days_observed": len(days),
    }


def _status_counts(records: list[dict], corrupt: int) -> dict:
    counts = {s: 0 for s in SNAPSHOT_STATUSES}
    counts["corrupt"] = corrupt
    for rec in records:
        for key in ("snapshot_status_before", "snapshot_status_after"):
            s = rec.get(key)
            if isinstance(s, str):
                counts[s] = counts.get(s, 0) + 1
    return counts


def _used_percent_stats(records: list[dict]) -> dict:
    peaks: list[int] = []
    for rec in records:
        for cl in rec.get("candidate_limits") or []:
            peak = _peak_used(cl)
            if peak is not None:
                peaks.append(peak)
    return {
        "samples": len(peaks),
        "max": max(peaks) if peaks else None,
        "count_ge_60": sum(1 for p in peaks if p >= THRESHOLD_LOW),
        "count_ge_70": sum(1 for p in peaks if p >= THRESHOLD_HIGH),
    }


def _delta_stats(records: list[dict]) -> dict:
    total = 0
    non_null = 0
    for rec in records:
        for cl in rec.get("candidate_limits") or []:
            total += 1
            if cl.get("observed_account_delta_during_interval") is not None:
                non_null += 1
    null_n = total - non_null
    return {
        "interpretation": "interval_correlation_not_attributed_usage",
        "total": total,
        "non_null": non_null,
        "null": null_n,
        "null_fraction": round(null_n / total, 4) if total else None,
    }


def build_report(
    records: Iterable[dict],
    *,
    store_id: str | None = None,
    corrupt_record_count: int = 0,
) -> dict:
    """Build the full non-authoritative evidence report over a record stream."""
    records = list(records)
    elig = check_eligibility(records)
    rec = recommendation(records, eligible=elig["eligible"])

    timestamps = [t for t in (_parse_ts(r.get("started_at")) for r in records) if t is not None]
    time_range = {
        "start": min(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ") if timestamps else None,
        "end": max(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ") if timestamps else None,
    }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "store_id": store_id,
        "generated_at": None,  # stamped by the writer (writer controls the clock)
        "status": "decision_required",
        "eligibility": elig,
        "recommendation": rec["recommendation"],
        "recommendation_detail": rec,
        "coverage": _coverage(records),
        "snapshot_status_counts": _status_counts(records, corrupt_record_count),
        "used_percent": _used_percent_stats(records),
        "delta_correlation": _delta_stats(records),
        "time_range": time_range,
        "input_record_count": len(records),
        "corrupt_record_count": corrupt_record_count,
        "records_digest": _records_digest(records),
    }


# ---------------------------------------------------------------------------
# Atomic, idempotent report writing + top-level summarize
# ---------------------------------------------------------------------------

REPORT_JSON = "quota-report.json"
REPORT_MD = "quota-report.md"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_atomic_0600(path: str, data: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _render_markdown(report: dict) -> str:
    elig = report.get("eligibility", {})
    rec = report.get("recommendation_detail", {})
    up = report.get("used_percent", {})
    lines = [
        "# Quota observation report",
        "",
        f"- **status:** {report.get('status')}",
        f"- **recommendation:** {report.get('recommendation')}",
        f"- **generated_at:** {report.get('generated_at')}",
        f"- **records_digest:** {report.get('records_digest')}",
        f"- **input records:** {report.get('input_record_count')} "
        f"(corrupt skipped: {report.get('corrupt_record_count')})",
        "",
        "## Eligibility",
        f"- eligible: {elig.get('eligible')}",
        f"- days spanned: {elig.get('days_spanned')}",
        f"- intervals: {elig.get('interval_count')}",
        f"- review kinds: {', '.join(elig.get('review_kinds', [])) or 'none'}",
        f"- unmet: {elig.get('unmet') or 'none'}",
        "",
        "## Signal (interval correlation, not attributed usage)",
        f"- used_percent max: {up.get('max')}",
        f"- samples >= 60: {up.get('count_ge_60')}",
        f"- samples >= 70: {up.get('count_ge_70')}",
        "",
        "## Recommendation rule applied",
        rec.get("rule", ""),
        "",
        "_This report is non-authoritative evidence for a separate product decision. "
        "It never enables a warning, changes thresholds, or alters review behavior._",
    ]
    return "\n".join(lines) + "\n"


def write_report(
    report: dict,
    out_dir: str,
    *,
    preserve_previous: bool = False,
    now_fn: Callable[[], datetime] | None = None,
) -> dict:
    """Atomically write the JSON + Markdown report at mode 0600.

    When ``preserve_previous`` is set, an existing report is rotated aside (kept
    under the same retention discipline as the log) before the new one is written.
    """
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    now = (now_fn or _utc_now)()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    json_path = os.path.join(out_dir, REPORT_JSON)
    md_path = os.path.join(out_dir, REPORT_MD)
    if preserve_previous:
        for path in (json_path, md_path):
            if os.path.exists(path):
                os.replace(path, os.path.join(out_dir, f"{path[:-5]}.{stamp}{os.path.splitext(path)[1]}"))
    report = {**report, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    _write_atomic_0600(json_path, json.dumps(report, indent=2, sort_keys=True))
    _write_atomic_0600(md_path, _render_markdown(report))
    return {"written": True, "json_path": json_path, "md_path": md_path}


def _read_records(log_path: str) -> tuple[list[dict], int]:
    records: list[dict] = []
    corrupt = 0
    if not os.path.isfile(log_path):
        return records, corrupt
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    corrupt += 1
                    continue
                if isinstance(rec, dict) and rec.get("invocation_id"):
                    records.append(rec)
                else:
                    corrupt += 1
    except OSError:
        return records, corrupt
    return records, corrupt


def _read_existing_report(out_dir: str) -> dict | None:
    path = os.path.join(out_dir, REPORT_JSON)
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def summarize(
    log_path: str,
    *,
    out_dir: str | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict:
    """Read the observation log, check eligibility, and (re)write the report.

    Before eligibility, no report is emitted and ``status`` is
    ``continue_observation``. Once eligible, exactly one current report is kept;
    re-running with an unchanged record set is a no-op (idempotent on digest),
    and a changed record set replaces it atomically. Never raises into the review
    path.
    """
    out_dir = out_dir or os.path.dirname(os.path.abspath(log_path))
    store_id = hashlib.sha256(os.path.abspath(log_path).encode("utf-8")).hexdigest()[:16]
    records, corrupt = _read_records(log_path)
    elig = check_eligibility(records)
    if not elig["eligible"]:
        return {
            "status": "continue_observation",
            "written": False,
            "eligible": False,
            "eligibility": elig,
            "corrupt_record_count": corrupt,
        }

    report = build_report(records, store_id=store_id, corrupt_record_count=corrupt)
    existing = _read_existing_report(out_dir)
    if existing and existing.get("records_digest") == report["records_digest"]:
        return {
            "status": "decision_required",
            "written": False,
            "reason": "unchanged",
            "report": existing,
        }

    write_result = write_report(
        report, out_dir, preserve_previous=existing is not None, now_fn=now_fn
    )
    written_report = _read_existing_report(out_dir) or report
    return {
        "status": "decision_required",
        "written": write_result["written"],
        "report": written_report,
        **write_result,
    }
