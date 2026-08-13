#!/usr/bin/env python3
"""Tests for read_codex_quota.py — observation-mode quota reader (no gating).

Run: `python3.11 -m pytest scripts/test_codex_quota_snapshot.py -q` from the skill root.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_codex_quota as rcq  # noqa: E402
import quota_observation as qo  # noqa: E402

SALT = bytes.fromhex("02" * 32)


# ---------------------------------------------------------------------------
# _read_jsonrpc_response (pure, injectable read_line)
# ---------------------------------------------------------------------------

def test_response_matches_id_skipping_notifications():
    lines = iter([
        '{"jsonrpc":"2.0","method":"account/rateLimits/updated","params":{}}',  # notification
        '{"jsonrpc":"2.0","id":1,"result":{"rateLimits":{}}}',                  # our answer
    ])

    def read_line():
        return next(lines, None)

    res = rcq._read_jsonrpc_response(read_line, expected_id=1)
    assert res == {"rateLimits": {}}


def test_response_malformed_line_raises():
    def read_line():
        return "{ not json"

    try:
        rcq._read_jsonrpc_response(read_line, expected_id=1)
    except rcq.QuotaReadError:
        pass
    else:
        assert False, "expected QuotaReadError"


def test_response_eof_raises():
    def read_line():
        return None

    try:
        rcq._read_jsonrpc_response(read_line, expected_id=1)
    except rcq.QuotaReadError:
        pass
    else:
        assert False, "expected QuotaReadError on EOF"


def test_response_method_not_found_is_unavailable():
    def read_line():
        return json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no"}})

    try:
        rcq._read_jsonrpc_response(read_line, expected_id=1)
    except rcq.QuotaUnavailable:
        pass
    else:
        assert False, "expected QuotaUnavailable for -32601"


def test_response_other_error_is_incompatible():
    def read_line():
        return json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}})

    try:
        rcq._read_jsonrpc_response(read_line, expected_id=1)
    except rcq.QuotaIncompatible:
        pass
    else:
        assert False, "expected QuotaIncompatible for non-method error"


# ---------------------------------------------------------------------------
# read_quota (injectable call)
# ---------------------------------------------------------------------------

def _rl(used=42, *, limit_id="codex", mins=10080):
    return {"rateLimits": {"limitId": limit_id,
                           "primary": {"usedPercent": used, "resetsAt": 1000, "windowDurationMins": mins}}}


def _acct(email="alice@example.com"):
    return {"account": {"email": email}}


def _call_factory(rl_result=None, rl_exc=None, acct_result=None, acct_exc=None):
    def call(method, params):
        if method == rcq.RATE_LIMITS_METHOD:
            if rl_exc:
                raise rl_exc
            return rl_result
        if method == rcq.ACCOUNT_METHOD:
            if acct_exc:
                raise acct_exc
            return acct_result
        raise AssertionError(f"unexpected method {method}")
    return call


def test_read_quota_ok_with_pseudonym():
    snap = rcq.read_quota(call=_call_factory(rl_result=_rl(42), acct_result=_acct()), salt=SALT)
    assert snap["status"] == "ok"
    assert snap["account_pseudonym"] == qo.account_pseudonym("alice@example.com", SALT)
    assert snap["candidates"][0]["used_percent"] == 42


def test_read_quota_ambiguous():
    snap = rcq.read_quota(call=_call_factory(rl_result={"rateLimits": {"primary": {"usedPercent": 5}}}),
                          salt=SALT)
    assert snap["status"] == "ambiguous"


def test_read_quota_unavailable():
    snap = rcq.read_quota(call=_call_factory(rl_exc=rcq.QuotaUnavailable("no rpc")), salt=SALT)
    assert snap["status"] == "unavailable"
    assert snap["candidates"] == []
    assert snap["account_pseudonym"] is None


def test_read_quota_incompatible():
    snap = rcq.read_quota(call=_call_factory(rl_exc=rcq.QuotaIncompatible("bad")), salt=SALT)
    assert snap["status"] == "incompatible"


def test_read_quota_error_on_timeout():
    snap = rcq.read_quota(call=_call_factory(rl_exc=rcq.QuotaReadError("timed out")), salt=SALT)
    assert snap["status"] == "error"


def test_read_quota_account_degraded_still_ok():
    # rate-limits readable, but account/read fails -> pseudonym None, status ok.
    snap = rcq.read_quota(call=_call_factory(rl_result=_rl(10), acct_exc=rcq.QuotaReadError("x")),
                          salt=SALT)
    assert snap["status"] == "ok"
    assert snap["account_pseudonym"] is None
    assert snap["candidates"][0]["used_percent"] == 10


def test_read_quota_no_salt_records_metric_null_id():
    snap = rcq.read_quota(call=_call_factory(rl_result=_rl(10), acct_result=_acct()), salt=None)
    assert snap["status"] == "ok"
    assert snap["candidates"][0]["candidate_id"] is None
    assert snap["account_pseudonym"] is None


# ---------------------------------------------------------------------------
# AppServerClient — real subprocess transport (echo server + timeout/orphan)
# ---------------------------------------------------------------------------

ECHO_SERVER = textwrap.dedent(
    """
    import sys, json
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if mid is None:
            continue
        if method == "account/rateLimits/read":
            result = {"rateLimits": {"limitId": "codex",
                    "primary": {"usedPercent": 42, "resetsAt": 1000, "windowDurationMins": 10080}}}
        elif method == "account/read":
            result = {"account": {"email": "alice@example.com"}}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


def _echo_cmd(tmp_path):
    script = tmp_path / "echo_server.py"
    script.write_text(ECHO_SERVER)
    return [sys.executable, str(script)]


def _pid_dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_appserver_client_round_trip(tmp_path):
    client = rcq.AppServerClient(codex_path="ignored", timeout=5.0, _cmd=_echo_cmd(tmp_path))
    try:
        snap = rcq.read_quota(call=client.call, salt=SALT)
    finally:
        client.close()
    assert snap["status"] == "ok"
    assert snap["candidates"][0]["used_percent"] == 42
    assert snap["account_pseudonym"] == qo.account_pseudonym("alice@example.com", SALT)


def test_appserver_client_timeout_kills_group(tmp_path):
    client = rcq.AppServerClient(
        codex_path="ignored", timeout=0.5,
        _cmd=[sys.executable, "-c", "import time; time.sleep(60)"],
    )
    snap = rcq.read_quota(call=client.call, salt=SALT)
    assert snap["status"] == "error"  # read timed out -> fail-soft error snapshot
    pid = client._proc.pid
    client.close()
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not _pid_dead(pid):
        time.sleep(0.1)
    assert _pid_dead(pid), "app-server orphaned after timeout"


def test_appserver_client_missing_is_unavailable():
    client = rcq.AppServerClient(codex_path="/no/such/codex-bin")
    try:
        snap = rcq.read_quota(call=client.call, salt=SALT)
    finally:
        client.close()
    assert snap["status"] == "unavailable"


def test_capture_snapshot_convenience(tmp_path):
    snap = rcq.capture_snapshot(
        codex_path="ignored", salt=SALT, timeout=5.0, _cmd=_echo_cmd(tmp_path)
    )
    assert snap["status"] == "ok"
    assert snap["candidates"][0]["used_percent"] == 42


def test_clamp_timeout_enforces_budget():
    # default 5s allowed, above the 15s hard max is clamped, tiny floors at 0.1s
    assert rcq._clamp_timeout(5.0) == 5.0
    assert rcq._clamp_timeout(15.0) == 15.0
    assert rcq._clamp_timeout(99.0) == rcq.MAX_QUOTA_TIMEOUT_SECONDS == 15.0
    assert rcq._clamp_timeout(0.0) == 0.1
