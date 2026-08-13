#!/usr/bin/env python3
"""Tests for codex_process.py — process-group lifecycle helper.

Run: `python3.11 -m pytest scripts/test_codex_process.py -q` from the skill root.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_process as cp  # noqa: E402


def _pid_dead(pid: int) -> bool:
    """True if ``pid`` no longer exists (has been reaped / does not run)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


# ---------------------------------------------------------------------------
# kill_process_group
# ---------------------------------------------------------------------------

def test_kill_process_group_reaps_single_process(tmp_path):
    proc = subprocess.Popen(
        ["sleep", "30"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert proc.poll() is None
    cp.kill_process_group(proc, grace=1.0)
    assert proc.returncode is not None  # reaped


def test_kill_process_group_reaps_descendants_no_orphans(tmp_path):
    """A grandchild in the same session must not survive group termination."""
    pidfile = tmp_path / "child.txt"
    script = textwrap.dedent(
        f"""
        python3 -c "import os,sys,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(30)" &
        sleep 30
        """
    ).strip()
    proc = subprocess.Popen(
        ["sh", "-c", script], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Wait for the grandchild to record its pid.
    for _ in range(50):
        if pidfile.exists():
            break
        time.sleep(0.1)
    grandchild = int(pidfile.read_text().strip())
    assert not _pid_dead(grandchild)  # grandchild is alive before termination

    cp.kill_process_group(proc, grace=2.0)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not _pid_dead(grandchild):
        time.sleep(0.1)
    assert _pid_dead(grandchild), "grandchild survived group termination (orphan)"


def test_kill_process_group_idempotent(tmp_path):
    proc = subprocess.Popen(
        ["sleep", "30"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cp.kill_process_group(proc, grace=1.0)
    # Calling again on an already-reaped process must not raise.
    cp.kill_process_group(proc, grace=1.0)
    assert proc.returncode is not None


def test_kill_process_group_uses_term_then_kill(tmp_path):
    """On POSIX the helper SIGTERMs the group, then SIGKILLs after the grace period.

    The child ignores SIGTERM so the escalation to SIGKILL is actually exercised.
    """
    script = (
        "python3 -c \"import signal,time; signal.signal(signal.SIGTERM, lambda *a: None); "
        "time.sleep(30)\""
    )
    proc = subprocess.Popen(
        ["sh", "-c", script], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    signaled: list[int] = []
    real_killpg = os.killpg

    def spy_killpg(pgid, sig):
        signaled.append(int(sig))
        return real_killpg(pgid, sig)

    og = os.killpg
    os.killpg = spy_killpg  # type: ignore[assignment]
    try:
        cp.kill_process_group(proc, grace=0.2)
    finally:
        os.killpg = og  # type: ignore[assignment]
    assert signal.SIGTERM in signaled or signal.SIGINT in signaled
    assert signal.SIGKILL in signaled
    assert proc.returncode is not None


# ---------------------------------------------------------------------------
# run_one_shot
# ---------------------------------------------------------------------------

def test_run_one_shot_success():
    cp = sys.modules["codex_process"]
    res = cp.run_one_shot(["printf", "hello\\n"], timeout=10.0)
    assert res.returncode == 0
    assert res.stdout == "hello\n"
    assert res.stderr == ""


def test_run_one_shot_nonzero_returncode():
    res = cp.run_one_shot(["sh", "-c", "exit 7"], timeout=10.0)
    assert res.returncode == 7


def test_run_one_shot_timeout_raises_and_kills_group(tmp_path):
    """On timeout the whole group is reaped: no orphan descendants survive."""
    pidfile = tmp_path / "gc.txt"
    script = (
        "python3 -c \"import os,time; open("
        + repr(str(pidfile))
        + ",'w').write(str(os.getpid())); time.sleep(60)\" & wait"
    )
    try:
        cp.run_one_shot(["sh", "-c", script], timeout=0.6, grace=1.0)
    except subprocess.TimeoutExpired:
        pass
    else:
        assert False, "expected TimeoutExpired"

    assert pidfile.exists(), "grandchild never recorded its pid"
    grandchild = int(pidfile.read_text().strip())
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not _pid_dead(grandchild):
        time.sleep(0.1)
    assert _pid_dead(grandchild), "grandchild orphaned by run_one_shot timeout"


def test_run_one_shot_timeout_carries_partial_output(tmp_path):
    script = "echo started; sleep 30"
    try:
        cp.run_one_shot(["sh", "-c", script], timeout=0.6, grace=1.0)
    except subprocess.TimeoutExpired as exc:
        assert "started" in (exc.stdout or "")
    else:
        assert False, "expected TimeoutExpired"
