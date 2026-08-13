#!/usr/bin/env python3
"""Shared process-group lifecycle helper for the Codex transport.

Centralizes one reliability concern that is independent of quota policy:
every Codex subprocess (the review, the doctor, and the quota reader's
``codex app-server``) runs as its own session/process-group leader, so that on
timeout or cancellation the *entire* group can be terminated and reaped without
orphaning descendant processes. This backports the latent Phase 5 orphan fix
(Phase 6 constraint 5 / risk #2) to the existing review and doctor timeout paths.

The helpers are deliberately small and dependency-free.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Sequence

# Grace period between the soft (SIGTERM) and hard (SIGKILL) group signals.
DEFAULT_GRACE_SECONDS = 2.0


def kill_process_group(
    proc: subprocess.Popen,
    *,
    grace: float = DEFAULT_GRACE_SECONDS,
    term_signal: int = signal.SIGTERM,
    kill_signal: int = signal.SIGKILL,
) -> None:
    """Terminate and reap ``proc`` and its whole process group.

    ``proc`` must have been started with ``start_new_session=True`` so it is the
    leader of its own process group (``pgid == proc.pid``). The group is signaled
    with ``term_signal``; if it has not been reaped within ``grace`` seconds, it
    is signaled with ``kill_signal``. Idempotent: an already-reaped process is a
    no-op. On non-POSIX hosts without ``os.killpg`` this falls back to terminating
    just the direct child.
    """
    if proc.poll() is not None:
        return  # already reaped; never signal a possibly-reused group id

    # ``start_new_session=True`` makes the child its own session/group leader, so
    # ``pgid == proc.pid``. Signaling the group reaches descendants a direct
    # ``proc.wait()`` would orphan (a leader can be reaped while survivors linger).
    pgid = proc.pid

    if hasattr(os, "killpg"):
        # Soft signal, bounded grace, then hard-kill the whole group, then reap.
        # PermissionError is tolerated alongside ProcessLookupError: once a group
        # has fully exited its pgid may be recycled by the OS to a group we cannot
        # signal, which is harmless (the group we cared about is already gone).
        try:
            os.killpg(pgid, term_signal)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(pgid, kill_signal)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=max(grace, 1.0))
        except subprocess.TimeoutExpired:
            pass
    else:  # pragma: no cover - non-POSIX fallback
        for fn in (proc.terminate, proc.kill):
            fn()
            try:
                proc.wait(timeout=grace)
                return
            except subprocess.TimeoutExpired:
                continue


def run_one_shot(
    cmd: Sequence[str],
    *,
    timeout: float,
    grace: float = DEFAULT_GRACE_SECONDS,
    stdin: int = subprocess.DEVNULL,
    text: bool = True,
    env: dict[str, str] | None = None,
    popen: type[subprocess.Popen] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` in its own session with a bounded timeout; reap the whole group.

    On timeout the entire process group is terminated/reaped via
    :func:`kill_process_group` (no orphans), then ``subprocess.TimeoutExpired`` is
    raised carrying the partial ``stdout``/``stderr`` captured before the kill —
    so callers' existing ``except subprocess.TimeoutExpired`` handlers work
    unchanged while gaining correct group cleanup.

    ``popen`` is injectable for tests; production uses the real ``subprocess.Popen``.
    """
    popen_cls = popen or subprocess.Popen
    proc = popen_cls(
        list(cmd),
        start_new_session=True,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the whole group FIRST (descendants included). Killing closes the
        # child's ends of the pipes, so the drain below reaches EOF instead of
        # blocking on a still-running producer.
        kill_process_group(proc, grace=grace)
        try:
            stdout, stderr = proc.communicate(timeout=max(grace, 1.0))
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            cmd=list(cmd), timeout=timeout, output=stdout, stderr=stderr
        )
    return subprocess.CompletedProcess(
        args=list(cmd), returncode=proc.returncode, stdout=stdout, stderr=stderr
    )
