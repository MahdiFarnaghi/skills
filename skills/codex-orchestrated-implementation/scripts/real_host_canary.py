#!/usr/bin/env python3
"""Opt-in real-host continuation canary.

This entry point deliberately does not emulate Codex or Claude.  A host CI
runner supplies the tracked-worker harness and enables it explicitly; local
quick validation reports the capability limitation instead of treating unit
tests as host evidence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


STAGES = ("implementation", "review", "correction", "rereview", "verification", "terra_acceptance")


def validate_report(report: object) -> dict:
    if not isinstance(report, dict):
        raise ValueError("canary report must be an object")
    if report.get("status") != "passed":
        raise ValueError("canary report status must be passed")
    if report.get("disposable_worktree") is not True or not isinstance(report.get("worktree"), str) or not Path(report["worktree"]).is_absolute():
        raise ValueError("canary must identify a disposable absolute worktree")
    if report.get("user_wakeup_count") != 0 or report.get("user_message_count") != 0:
        raise ValueError("canary must complete without user wakeups or messages")
    events = report.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("canary must report the ordered host event trace")
    stages = report.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(STAGES):
        raise ValueError("canary must report all six lifecycle stages")
    for stage in STAGES:
        value = stages[stage]
        if not isinstance(value, dict) or not isinstance(value.get("job_id"), str) or value.get("dispatch_count", 0) < 1 or value.get("wait_count", 0) < 1 or value.get("receipt_consumed") is not True:
            raise ValueError(f"canary stage {stage} lacks dispatch/wait/receipt evidence")
    return report


def main() -> int:
    enabled = os.environ.get("CODEX_ORCHESTRATION_REAL_HOST_CANARY") == "1"
    if not enabled:
        print(json.dumps({"status": "skipped", "reason": "real Codex/Claude tracked-worker credentials and host wakeup are unavailable locally", "user_wakeup_count": 0, "user_message_count": 0}))
        return 0
    harness = os.environ.get("CODEX_ORCHESTRATION_CANARY_HARNESS")
    if not harness:
        print("real_host_canary: CODEX_ORCHESTRATION_CANARY_HARNESS is required when enabled", file=sys.stderr)
        return 2
    # The host runner owns actual dispatch/wait calls.  It must emit a JSON
    # report with the complete event chain and zero user wakeups.
    import subprocess
    result = subprocess.run([harness], capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("real_host_canary: harness did not return JSON", file=sys.stderr)
        return 2
    try:
        report = validate_report(report)
    except ValueError as exc:
        print(f"real_host_canary: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
