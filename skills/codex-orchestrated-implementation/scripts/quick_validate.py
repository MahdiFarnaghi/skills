#!/usr/bin/env python3
"""Fast package sanity check used before the focused test suite."""

from __future__ import annotations

import json
import py_compile
import subprocess
import importlib.util
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        from validate_config import load_config

        resolved = load_config(ROOT / "config" / "example.toml")
        if resolved.policy["version"] != 2:
            raise ValueError("example policy is not v2")
        for path in (ROOT / "schemas").glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "scripts").glob("*.py"):
            py_compile.compile(str(path), doraise=True)
        # The quick check must remain usable in the host's bundled Python,
        # which intentionally may not carry pytest.  CI/test runners execute
        # the focused suite separately; here we still require the Phase 4
        # module to compile and expose its guard protocol.
        if importlib.util.find_spec("pytest") is not None:
            phase4 = ROOT / "scripts" / "test_phase4.py"
            completed = subprocess.run([sys.executable, "-m", "pytest", "-q", str(phase4)], cwd=ROOT.parent.parent, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(f"Phase 4 tests failed: {completed.stdout[-1000:]}{completed.stderr[-1000:]}")
        from orchestration_state import OrchestrationStore
        with tempfile.TemporaryDirectory() as directory:
            OrchestrationStore(directory).create(task_id="quick", milestone_id="m1")
            guard = ROOT / "scripts" / "continuation_guard.py"
            smoke = subprocess.run([sys.executable, str(guard), "--store", directory, "next-action"], capture_output=True, text=True)
            if smoke.returncode != 0 or '"action"' not in smoke.stdout:
                raise RuntimeError(f"continuation guard CLI smoke test failed: {smoke.stderr}")
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"quick_validate: failed: {exc}", file=sys.stderr)
        return 2
    print("quick_validate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
