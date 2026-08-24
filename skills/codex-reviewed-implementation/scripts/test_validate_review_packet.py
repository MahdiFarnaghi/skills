"""Tests for compact packet validation and scaffold generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import validate_review_packet as vrp  # noqa: E402


def test_flat_milestone_packet_is_valid():
    packet = """# Milestone review

Milestone: M1
Baseline: abc123
Scope:
- core/foo.py:Foo
Contract:
- returns the value
Non-goals:
- migration
Verification:
- pytest -q
Evidence gaps:
- none
Directed questions:
- none
"""
    kind, errors = vrp.validate(packet)
    assert kind == "milestone"
    assert errors == []


def test_generated_milestone_scaffold_is_valid(tmp_path):
    output = tmp_path / "packet.md"
    args = argparse.Namespace(
        kind="milestone", milestone="M1", baseline="abc123",
        scope=["core/foo.py:Foo"], output=output,
    )
    assert vrp._init_packet(args) == 0
    kind, errors = vrp.validate(output.read_text(encoding="utf-8"))
    assert kind == "milestone"
    assert errors == []


def test_packet_limit_is_four_thousand_characters():
    assert vrp.MAX_CHARACTERS == 4_000
