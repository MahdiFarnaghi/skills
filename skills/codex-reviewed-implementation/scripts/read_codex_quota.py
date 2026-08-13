#!/usr/bin/env python3
"""Observation-mode Codex quota reader (Phase 6).

Reads ``account/rateLimits/read`` (and ``account/read`` for account identity)
from the experimental ``codex app-server`` JSON-RPC surface, **purely for
observation**. A snapshot is metadata attached to a review; it can never gate,
block, cancel, retry, or reorder a review. Every failure is fail-soft: a missing
app-server, missing RPC, malformed JSON-RPC, incompatible protocol, ambiguous
limits, or reader timeout records a degraded snapshot (``snapshot=none`` /
``unavailable`` / ``incompatible`` / ``ambiguous`` / ``error``) and the review
proceeds identically.

Transport facts (codex app-server, experimental):
- default transport is JSON-RPC 2.0 over stdio, newline-delimited JSON;
- method ``account/rateLimits/read`` returns ``GetAccountRateLimitsResponse``
  with a single-bucket ``rateLimits`` and a multi-bucket ``rateLimitsByLimitId``;
- each window carries an integer ``usedPercent`` (no fractional precision),
  ``resetsAt`` (unix seconds|null) and ``windowDurationMins`` (int|null).
- the ``10080``-minutes-means-weekly assumption is NOT used anywhere here.

The JSON-RPC parsing is split from the subprocess transport so it is unit
testable without a real Codex call: :func:`_read_jsonrpc_response` consumes an
injectable ``read_line`` callable.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

# Local, dependency-free helpers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_process  # noqa: E402
import quota_observation as qo  # noqa: E402

DEFAULT_QUOTA_TIMEOUT_SECONDS = 5.0
MAX_QUOTA_TIMEOUT_SECONDS = 15.0

RATE_LIMITS_METHOD = "account/rateLimits/read"
ACCOUNT_METHOD = "account/read"
JSONRPC_METHOD_NOT_FOUND = -32601


# ---------------------------------------------------------------------------
# Fail-soft exception vocabulary
# ---------------------------------------------------------------------------

class QuotaUnavailable(Exception):
    """No usable signal: app-server missing, unauthenticated, or RPC not present."""


class QuotaIncompatible(Exception):
    """The protocol responded but in a version/shape this reader cannot trust."""


class QuotaReadError(Exception):
    """Generic read failure: timeout, I/O error, malformed framing."""


# ---------------------------------------------------------------------------
# Pure JSON-RPC response parsing (injectable read_line)
# ---------------------------------------------------------------------------

def _read_jsonrpc_response(read_line: Callable[[], str | None], expected_id: int) -> dict:
    """Consume NDJSON lines until the response with ``expected_id`` arrives.

    Notifications (no ``id``) and unrelated messages are skipped. Maps outcomes:
    EOF / malformed JSON / unmatched id exhaustion -> :class:`QuotaReadError`;
    a JSON-RPC ``error`` with code -32601 -> :class:`QuotaUnavailable`; any other
    error -> :class:`QuotaIncompatible`. Returns the ``result`` object on success.
    """
    while True:
        raw = read_line()
        if raw is None:
            raise QuotaReadError("app-server stream ended before a matching response")
        try:
            msg = json.loads(raw)
        except ValueError as exc:
            raise QuotaReadError(f"malformed JSON-RPC line: {exc}") from exc
        if not isinstance(msg, dict):
            raise QuotaReadError("non-object JSON-RPC message")
        if msg.get("id") != expected_id:
            continue  # a notification or someone else's response
        if "error" in msg and msg["error"] is not None:
            code = msg["error"].get("code") if isinstance(msg["error"], dict) else None
            if code == JSONRPC_METHOD_NOT_FOUND:
                raise QuotaUnavailable(f"RPC method not found: {msg['error']}")
            raise QuotaIncompatible(f"JSON-RPC error: {msg['error']}")
        result = msg.get("result")
        if not isinstance(result, dict):
            raise QuotaIncompatible("JSON-RPC response result is not an object")
        return result


# ---------------------------------------------------------------------------
# read_quota — the observation snapshot (transport injected as ``call``)
# ---------------------------------------------------------------------------

def _snap(status: str, diagnostic: str = "") -> dict:
    return {
        "status": status,
        "account_pseudonym": None,
        "candidates": [],
        "diagnostic": diagnostic,
    }


def read_quota(*, call: Callable[[str, dict], dict], salt: bytes | None = None) -> dict:
    """Capture one observation-only quota snapshot via an injected ``call``.

    ``call(method, params)`` performs a single JSON-RPC round trip and returns the
    ``result`` object, raising :class:`QuotaUnavailable` / :class:`QuotaIncompatible`
    / :class:`QuotaReadError` on the corresponding failures. This function never
    raises: every failure path returns a degraded snapshot so the caller can record
    ``snapshot=none``-equivalent metadata and proceed without altering the review.
    """
    try:
        result = call(RATE_LIMITS_METHOD, {})
    except QuotaUnavailable as exc:
        return _snap("unavailable", str(exc))
    except QuotaIncompatible as exc:
        return _snap("incompatible", str(exc))
    except Exception as exc:  # timeout, I/O, anything else
        return _snap("error", _safe_reason(exc))

    if not isinstance(result, dict):
        return _snap("incompatible", "rateLimits result is not an object")

    candidates, norm_status = qo.normalize_candidates(result, salt)

    # Account pseudonym is best-effort and must never fail the snapshot.
    account_pseudonym = None
    try:
        acct_resp = call(ACCOUNT_METHOD, {})
        identity = None
        if isinstance(acct_resp, dict):
            acct_obj = acct_resp.get("account")
            if isinstance(acct_obj, dict):
                identity = acct_obj.get("email")
        account_pseudonym = qo.account_pseudonym(identity, salt)
    except Exception:
        account_pseudonym = None  # degraded: no pseudonym, observation continues

    return {
        "status": norm_status,  # "ok" or "ambiguous"
        "account_pseudonym": account_pseudonym,
        "candidates": candidates,
        "diagnostic": "",
    }


def _safe_reason(exc: Exception) -> str:
    name = type(exc).__name__
    return f"{name}: {exc}" if str(exc) else name


def _clamp_timeout(timeout: float) -> float:
    """Observation latency budget: default 5s, hard max 15s (plan constraint 7)."""
    return max(0.1, min(float(timeout), MAX_QUOTA_TIMEOUT_SECONDS))


# ---------------------------------------------------------------------------
# AppServerClient — production JSON-RPC transport over `codex app-server`
# ---------------------------------------------------------------------------

class AppServerClient:
    """A bounded, process-group-isolated ``codex app-server`` JSON-RPC client.

    ``call(method, params)`` performs one newline-delimited JSON-RPC round trip
    against the spawned app-server, subject to the configured (clamped) timeout.
    On timeout the read raises :class:`QuotaReadError`; :meth:`close` terminates
    and reaps the whole process group so no descendants are orphaned.

    ``_cmd`` overrides the spawned command so tests can point at a fixture server
    instead of the real Codex CLI; ``popen`` is injectable likewise.
    """

    def __init__(
        self,
        *,
        codex_path: str,
        codex_home: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_QUOTA_TIMEOUT_SECONDS,
        popen: Any = None,
        _cmd: Sequence[str] | None = None,
    ):
        self._timeout = _clamp_timeout(timeout)
        self._popen = popen or subprocess.Popen
        self._codex_home = codex_home
        self._cmd = list(_cmd) if _cmd is not None else self._build_cmd(codex_path, model)
        self._proc: subprocess.Popen | None = None
        self._next_id = 1

    def _build_cmd(self, codex_path: str, model: str | None) -> list[str]:
        cmd = [codex_path, "app-server"]
        if model:
            # codex -c takes a TOML value; quote the model string.
            cmd += ["-c", f'model="{model}"']
        return cmd

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        env = os.environ.copy()
        if self._codex_home:
            env["CODEX_HOME"] = str(self._codex_home)
        try:
            self._proc = self._popen(
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise QuotaUnavailable(f"codex app-server not found: {exc}") from exc
        except OSError as exc:  # pragma: no cover - rare
            raise QuotaReadError(f"could not start app-server: {exc}") from exc

    def _read_line_with_deadline(self, deadline: float) -> str | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QuotaReadError("quota read timed out")
        assert self._proc is not None and self._proc.stdout is not None
        try:
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
        except (OSError, ValueError) as exc:
            raise QuotaReadError(f"select failed: {exc}") from exc
        if not ready:
            raise QuotaReadError("quota read timed out")
        line = self._proc.stdout.readline()
        return line if line else None

    def call(self, method: str, params: dict) -> dict:
        self._ensure()
        assert self._proc is not None and self._proc.stdin is not None
        req_id = self._next_id
        self._next_id += 1
        request = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
        try:
            self._proc.stdin.write(request)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise QuotaReadError(f"app-server stream closed: {exc}") from exc
        deadline = time.monotonic() + self._timeout
        return _read_jsonrpc_response(lambda: self._read_line_with_deadline(deadline), req_id)

    def close(self) -> None:
        if self._proc is not None:
            codex_process.kill_process_group(self._proc, grace=1.0)
            self._proc = None


def capture_snapshot(
    *,
    codex_path: str,
    codex_home: str | None = None,
    model: str | None = None,
    salt: bytes | None = None,
    timeout: float = DEFAULT_QUOTA_TIMEOUT_SECONDS,
    popen: Any = None,
    _cmd: Sequence[str] | None = None,
) -> dict:
    """Convenience wrapper: build an :class:`AppServerClient`, capture, and close.

    Always returns a snapshot dict (never raises): transport failures surface as
    ``unavailable`` / ``incompatible`` / ``error`` statuses. The client is closed
    (process group reaped) in all cases.
    """
    client = AppServerClient(
        codex_path=codex_path, codex_home=codex_home, model=model,
        timeout=timeout, popen=popen, _cmd=_cmd,
    )
    try:
        return read_quota(call=client.call, salt=salt)
    finally:
        client.close()
