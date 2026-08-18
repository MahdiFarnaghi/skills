#!/usr/bin/env python3
"""Quota observation: normalization, delta, and JSONL logging (Phase 6).

This module is **observation-only**. It never gates, blocks, cancels, retries, or
reorders a review. Its job is to turn a Codex account/rate-limits RPC response
into normalized, pseudonymized, stable records and to append them to a locked,
rotating JSONL store outside the reviewed worktree. Every failure path is
fail-soft: a problem here records a degraded record (or none) and never alters
the review outcome.

Privacy invariants (plan constraint 4): only normalized observational fields are
persisted. Account correlation uses a salted SHA-256 digest; raw ``orgId``,
``accountId``, ``email``, ``plan``, tokens, headers, and endpoint credentials are
never persisted. Limit labels (which may embed plan names) are also pseudonymized
into a stable candidate id; only structural, non-identifying fields (window role,
window duration, reset timestamp, used percent) are kept in the clear.

The quota *signal* itself (usedPercent etc.) is read elsewhere
(``read_codex_quota.py``); this module owns the pure normalization, delta, and
logging logic so it is fully unit-testable without a real Codex call.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat as _stat
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SALT_FILENAME = ".quota-obs-salt"
SALT_BYTES = 32
PSEUDONYM_HEX_LEN = 16  # 8 bytes — stable, short, non-identifying

DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB rotation threshold
DEFAULT_RETAIN_DAYS = 90
DEFAULT_MIN_FILES = 5  # always retain at least the N newest rotated files

# JSONL record schema version (observation records, independent of the verdict
# schema version).
OBSERVATION_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Pseudonym salt + account pseudonym
# ---------------------------------------------------------------------------

def ensure_salt(store_path: str) -> bytes | None:
    """Load (or create) the 32-byte pseudonym salt that lives beside the log.

    The salt is reused for one observation store and never copied into receipts
    or the repository. If it cannot be created or read safely, return ``None``
    so the caller records no account pseudonym and continues in degraded mode.
    """
    salt_path = os.path.join(os.path.dirname(os.path.abspath(store_path)), SALT_FILENAME)
    try:
        os.makedirs(os.path.dirname(salt_path), exist_ok=True)
        if os.path.isfile(salt_path):
            data = _read_salt(salt_path)
            if data:
                return data
        data = os.urandom(SALT_BYTES)
        fd = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        # Ensure mode is 0600 even if the file pre-existed with broader bits.
        os.chmod(salt_path, 0o600)
        return data
    except OSError:
        return None


def _read_salt(salt_path: str) -> bytes | None:
    try:
        st = os.stat(salt_path)
    except OSError:
        return None
    # Refuse to reuse a salt file that is group/world readable (secret hygiene).
    if _stat.S_IMODE(st.st_mode) != 0o600:
        return None
    try:
        data = Path(salt_path).read_bytes()
    except OSError:
        return None
    return data if len(data) == SALT_BYTES else None


def _digest(value: str, salt: bytes) -> str:
    return hashlib.sha256(salt + value.encode("utf-8")).hexdigest()[:PSEUDONYM_HEX_LEN]


def account_pseudonym(account_identity: str | None, salt: bytes | None) -> str | None:
    """Stable salted digest of a server-returned account identity (e.g. email).

    Returns ``None`` when the identity or salt is absent, so the record simply
    omits the account pseudonym rather than failing.
    """
    if not account_identity or not salt:
        return None
    return _digest(account_identity.strip().lower(), salt)


def candidate_pseudonym(limit_key: str | None, window_role: str, salt: bytes | None) -> str | None:
    """Stable pseudonymized id for a rate-limit candidate + window.

    ``limit_key`` is the structural identity of the limit (the ``limitId``, the
    ``rateLimitsByLimitId`` map key, or ``limitName``). It is pseudonymized so no
    plan/meter label is persisted raw; the same inputs always produce the same id
    so before/after snapshots can be matched.
    """
    if not limit_key or not salt:
        return None
    return _digest(f"{limit_key}\x1f{window_role}", salt)


# Order matters only for determinism; both sources are recorded, never merged.
_WINDOW_ROLES = ("primary", "secondary")


def _snapshot_limit_key(map_key: str | None, snapshot: dict) -> str | None:
    """Pick the most stable structural identity for a rate-limit snapshot."""
    for field in ("limitId", "limitName"):
        val = snapshot.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(map_key, str) and map_key.strip():
        return map_key.strip()
    return None


def normalize_candidates(response: dict, salt: bytes | None) -> tuple[list[dict], str]:
    """Extract normalized, pseudonymized candidate limits from an RPC response.

    Records *every* window of *every* reported limit; it never chooses a
    "relevant" or "weekly" window (the ``windowDurationMins == 10080`` heuristic
    is forbidden). Returns ``(candidate_limits, status)`` where status is
    ``"ambiguous"`` when candidate identity is missing or duplicated (so before/
    after matching would be unstable), otherwise ``"ok"``.

    Each candidate limit carries: ``candidate_id`` (pseudonym or ``None`` in
    degraded/no-salt mode), ``window_role``, ``window_duration_mins``,
    ``resets_at``, and ``used_percent``.
    """
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    ambiguous = False

    # Build an ordered list of (map_key, snapshot) from both response shapes.
    sources: list[tuple[str | None, dict]] = []
    single = response.get("rateLimits")
    if isinstance(single, dict):
        sources.append((None, single))
    by_id = response.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        for key, snap in by_id.items():
            if isinstance(snap, dict):
                sources.append((str(key), snap))

    for map_key, snapshot in sources:
        limit_key = _snapshot_limit_key(map_key, snapshot)
        if limit_key is None:
            ambiguous = True  # identity missing -> unstable matching
        for role in _WINDOW_ROLES:
            window = snapshot.get(role)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if not isinstance(used, int):
                continue  # no usable metric for this window
            cid = candidate_pseudonym(limit_key, role, salt) if limit_key else None
            if cid is not None:
                if cid in seen_ids:
                    ambiguous = True  # duplicated identity
                seen_ids.add(cid)
            candidates.append({
                "candidate_id": cid,
                "window_role": role,
                "window_duration_mins": window.get("windowDurationMins"),
                "resets_at": window.get("resetsAt"),
                "used_percent": used,
            })

    return candidates, ("ambiguous" if ambiguous else "ok")


# ---------------------------------------------------------------------------
# Delta rules + before/after merge
# ---------------------------------------------------------------------------

def candidate_delta(before: dict, after: dict) -> int | None:
    """Correlation delta of ``used_percent`` for one matched candidate.

    This is interval correlation, NOT attributed review consumption. It is
    ``None`` whenever the comparison would be unstable or meaningless:

    - the window duration changed (different window kind), or
    - the window reset during the interval (``resets_at`` differs, indicating a
      rollover), or
    - ``resets_at`` is present on only one side, or
    - the counter decreased without a recorded reset.

    Otherwise it is ``after.used_percent - before.used_percent`` (which may be 0).
    """
    if before.get("window_duration_mins") != after.get("window_duration_mins"):
        return None
    rb, ra = before.get("resets_at"), after.get("resets_at")
    if rb is None and ra is None:
        pass
    elif rb is None or ra is None or rb != ra:
        return None
    if after.get("used_percent", 0) < before.get("used_percent", 0):
        return None
    return after.get("used_percent", 0) - before.get("used_percent", 0)


def _merged_entry(before: dict | None, after: dict | None, delta: int | None) -> dict:
    src = before or after or {}
    return {
        "candidate_id": src.get("candidate_id"),
        "window_role": src.get("window_role"),
        "window_duration_mins": src.get("window_duration_mins"),
        "used_percent_before": before.get("used_percent") if before else None,
        "used_percent_after": after.get("used_percent") if after else None,
        "resets_at_before": before.get("resets_at") if before else None,
        "resets_at_after": after.get("resets_at") if after else None,
        "observed_account_delta_during_interval": delta,
    }


def merge_candidate_limits(
    before_candidates: list[dict] | None,
    after_candidates: list[dict] | None,
    *,
    accounts_match: bool,
) -> list[dict]:
    """Pair before/after candidate limits by ``candidate_id`` and compute deltas.

    Applies the record-level rule that a differing account pseudonym forces every
    delta to ``None``. Candidates present on only one side are recorded with the
    other side null and a null delta. Candidates with a null id (degraded/no-salt
    mode) cannot be matched and are recorded individually with a null delta.
    """
    def _index(cands: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
        by_id: dict[str, list[dict]] = {}
        unmatchable: list[dict] = []
        for c in cands or []:
            cid = c.get("candidate_id")
            if cid is None:
                unmatchable.append(c)
            else:
                by_id.setdefault(cid, []).append(c)
        return by_id, unmatchable

    b_by, b_un = _index(before_candidates or [])
    a_by, a_un = _index(after_candidates or [])

    merged: list[dict] = []
    for cid in sorted(set(b_by) | set(a_by)):
        bs, as_ = b_by.get(cid, []), a_by.get(cid, [])
        # Ambiguity within a side (duplicate id) -> record without a delta.
        if len(bs) > 1 or len(as_) > 1:
            merged.append(_merged_entry(bs[0] if bs else None, as_[0] if as_ else None, None))
            continue
        b, a = (bs[0] if bs else None), (as_[0] if as_ else None)
        delta = candidate_delta(b, a) if (b and a and accounts_match) else None
        merged.append(_merged_entry(b, a, delta))

    for c in b_un + a_un:
        merged.append(_merged_entry(c, None, None) if c in b_un else _merged_entry(None, c, None))

    return merged


# ---------------------------------------------------------------------------
# Diagnostics bounding + redaction (never persist secrets/raw payloads)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|bearer|authorization)"
               r"(\s*[=:]\s*)(['\"]?)[A-Za-z0-9._~+/=-]{16,}\3"),
    re.compile(r"\b[a-f0-9]{40,}\b"),
]

MAX_DIAGNOSTIC_CHARS = 2000


def redact_and_bound(text: str | None, *, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """Redact likely-secret values and cap length before persisting diagnostics."""
    if not text:
        return ""

    def _sub(m: re.Match[str]) -> str:
        full = m.group(0)
        return "[REDACTED]" if len(full) <= 8 else full[:4] + "[REDACTED]"

    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_sub, out)
    if len(out) > limit:
        out = out[:limit] + "…[truncated]"
    return out


# ---------------------------------------------------------------------------
# JSONL logging: locked atomic append + rotation + retention
# ---------------------------------------------------------------------------

_ROTATED_RE = re.compile(r"^.+\.[0-9]+\.jsonl$")


def _lock_path(log_path: str) -> str:
    return log_path + ".lock"


def _stem(log_path: str) -> str:
    return log_path[: -len(".jsonl")] if log_path.endswith(".jsonl") else log_path


def _rotate(log_path: str, *, now_epoch: float) -> str | None:
    """Rename the current log to a timestamped rotated file; return its path.

    Uses an integer epoch suffix so rotated files sort deterministically and
    retention can compute age without relying solely on mtime.
    """
    if not os.path.isfile(log_path) or os.path.getsize(log_path) == 0:
        return None
    stamp = int(now_epoch)
    rotated = f"{_stem(log_path)}.{stamp}.jsonl"
    # If a rotation already happened this second, append a disambiguator.
    n = 1
    while os.path.exists(rotated):
        rotated = f"{_stem(log_path)}.{stamp}-{n}.jsonl"
        n += 1
    os.replace(log_path, rotated)
    return rotated


def _enforce_retention(
    log_path: str, *, retain_days: int, min_files: int, now_epoch: float
) -> int:
    """Delete rotated files older than ``retain_days``, keeping ``min_files`` newest.

    Returns the number of files deleted. Age is measured from each rotated file's
    mtime (the time it was actually written).
    """
    directory = os.path.dirname(os.path.abspath(log_path))
    base = os.path.basename(log_path)
    stem_prefix = os.path.basename(_stem(log_path)) + "."
    rotated: list[str] = []
    try:
        for name in os.listdir(directory):
            if name != base and name.startswith(stem_prefix) and _ROTATED_RE.match(name):
                rotated.append(os.path.join(directory, name))
    except OSError:
        return 0
    # Newest first by mtime.
    rotated.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    deleted = 0
    cutoff = now_epoch - retain_days * 86400
    for path in rotated[min_files:]:
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                deleted += 1
        except OSError:
            continue
    return deleted


def append_observation(
    log_path: str,
    record: dict,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    min_files: int = DEFAULT_MIN_FILES,
    now_epoch: "callable | None" = None,
) -> dict:
    """Append one observation record as a single JSON line, locked and atomic.

    Creates the log with mode ``0600``. Serializes concurrent writers with an
    ``fcntl`` file lock so records are never interleaved or lost. Rotates the log
    when it already exceeds ``max_bytes`` (``<= 0`` disables rotation) and prunes
    rotated files per the retention policy. All failures are swallowed and
    reported in the returned summary; this function never raises into the review
    path. ``now_epoch`` is an injectable ``() -> float`` clock for tests.
    """
    summary = {"appended": False, "rotated": False, "rotated_to": None, "error": None}
    if not isinstance(record, dict):
        summary["error"] = "record is not a dict"
        return summary
    now = (now_epoch or time.time)()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        lock_fd = os.open(_lock_path(log_path), os.O_RDWR | os.O_CREAT, 0o600)
        os.chmod(_lock_path(log_path), 0o600)
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                # Creation belongs inside the critical section. O_APPEND avoids
                # the first-writer TOCTOU truncation race.
                if not os.path.exists(log_path):
                    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.close(fd)
                if max_bytes and max_bytes > 0 and os.path.getsize(log_path) >= max_bytes:
                    rotated_to = _rotate(log_path, now_epoch=now)
                    if rotated_to:
                        summary["rotated"] = True
                        summary["rotated_to"] = rotated_to
                    # Recreate the (now-missing) current log at 0600.
                    if not os.path.exists(log_path):
                        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                        os.close(fd)
                _enforce_retention(log_path, retain_days=retain_days, min_files=min_files, now_epoch=now)
                line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                # Keep the log tightly permissioned even if touched elsewhere.
                os.chmod(log_path, 0o600)
                summary["appended"] = True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        summary["error"] = redact_and_bound(str(exc))
    return summary


# ---------------------------------------------------------------------------
# Full observation record assembly
# ---------------------------------------------------------------------------

# Metadata keys copied verbatim from the caller-supplied ``meta`` dict. These are
# the observational/context fields named in the Phase 6 "What to record" list.
META_FIELDS = (
    "invocation_id",
    "transport_version",
    "schema_digest",
    "doctor_receipt_digest",
    "codex_path",
    "codex_version",
    "model",
    # Phase 7 execution-profile fields — the resolved profile is recorded so
    # an observation is attributable to exactly what ran (never just "null").
    "reasoning_effort",
    "config_version",
    "resolution_source",
    "profile_digest",
    "review_kind",
    "review_phase",
    "milestone",
    "round",
    "started_at",
    "finished_at",
    "duration_s",
    "outcome",
    "verdict",
)


def build_observation_record(
    meta: dict, before_snapshot: dict | None, after_snapshot: dict | None
) -> dict:
    """Assemble one JSONL observation record from before/after snapshots + meta.

    ``before_snapshot``/``after_snapshot`` are dicts shaped as
    ``{"status", "account_pseudonym", "candidates", "diagnostic"}`` (or ``None``
    when that side could not be captured). The account pseudonyms determine
    whether candidate deltas are computed: a delta is only meaningful when both
    sides report the same non-null pseudonym.
    """
    bp = before_snapshot.get("account_pseudonym") if before_snapshot else None
    ap = after_snapshot.get("account_pseudonym") if after_snapshot else None
    accounts_match = bool(bp and ap and bp == ap)
    before_cands = before_snapshot.get("candidates") if before_snapshot else None
    after_cands = after_snapshot.get("candidates") if after_snapshot else None

    record: dict = {"schema_version": OBSERVATION_SCHEMA_VERSION}
    for key in META_FIELDS:
        if key in meta:
            record[key] = meta[key]
    record["account_pseudonym"] = bp or ap
    record["snapshot_status_before"] = before_snapshot.get("status") if before_snapshot else "none"
    record["snapshot_status_after"] = after_snapshot.get("status") if after_snapshot else "none"
    record["snapshot_diagnostic_before"] = redact_and_bound(
        before_snapshot.get("diagnostic") if before_snapshot else None
    ) or None
    record["snapshot_diagnostic_after"] = redact_and_bound(
        after_snapshot.get("diagnostic") if after_snapshot else None
    ) or None
    record["candidate_limits"] = merge_candidate_limits(
        before_cands, after_cands, accounts_match=accounts_match
    )
    return record
