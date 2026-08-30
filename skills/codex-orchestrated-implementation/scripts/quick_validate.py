#!/usr/bin/env python3
"""Fast package sanity check used before the focused test suite."""

from __future__ import annotations

import json
import py_compile
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
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"quick_validate: failed: {exc}", file=sys.stderr)
        return 2
    print("quick_validate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
