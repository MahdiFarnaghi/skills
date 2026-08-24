#!/usr/bin/env python3
"""Validate a compact Codex plan or milestone review packet.

Checks structure and completeness, not semantic truth. Rejects unresolved
template placeholders, empty required sections, and missing/invalid declared
fields, while leaving legitimate shell syntax, environment-variable notation,
Markdown links, and ordinary comparison text untouched.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PACKET_SCHEMAS = {
    "plan": (
        "# Codex plan review",
        "## Objective",
        "## Authority",
        "## Global invariants",
        "## Milestones",
        "## Integration strategy",
        "## Evidence strategy",
        "## Directed questions",
    ),
    "milestone_legacy": (
        "# Codex milestone review",
        "## Identity",
        "## Contract",
        "## Non-goals",
        "## Verification",
        "## Evidence status",
        "## Independent review mandate",
        "## Directed questions",
    ),
    "milestone": ("# Milestone review",),
}
MAX_CHARACTERS = 4_000
QUESTION_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
# An unresolved template placeholder: "<" directly followed by a letter, then
# descriptive words, then ">". Inline code is stripped first so legitimate
# shell snippets such as `sort -n < file` or `<remote>` inside backticks are
# not mistaken for placeholders. Comparisons (a < b) and autolinks with a
# scheme (http://) do not match because of the space or the colon.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9 _/|.-]*>")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
HEADING_RE = re.compile(r"^(#{1,6})\s+\S")

# Status taxonomy from references/evidence-ledger.md.
VALID_ACCEPTANCE_STATUSES = {
    "planned",
    "implemented",
    "focused_verified",
    "integration_verified",
    "system_verified",
    "partially_verified",
    "skipped",
    "blocked",
    "complete",
}
VALID_DIFF_MODES = ("isolated since baseline", "cumulative working tree")


def detect_kind(packet: str) -> str | None:
    if packet.startswith("# Codex plan review"):
        return "plan"
    if packet.startswith("# Milestone review"):
        return "milestone"
    if packet.startswith("# Codex milestone review"):
        return "milestone_legacy"
    return None


def _heading_level(line: str) -> int | None:
    match = HEADING_RE.match(line)
    if not match:
        return None
    return len(match.group(1))


def section_body(packet: str, heading: str) -> str | None:
    """Return body text under `heading` until the next heading of equal or higher level."""
    lines = packet.splitlines()
    start: int | None = None
    level: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            level = _heading_level(line)
            break
    if start is None or level is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        line_level = _heading_level(line)
        if line_level is not None and line_level <= level:
            break
        body.append(line)
    return "\n".join(body)


def directed_questions_section(packet: str) -> str:
    section = section_body(packet, "## Directed questions")
    return section if section is not None else ""


def _has_real_content(text: str | None) -> bool:
    if not text:
        return False
    stripped = INLINE_CODE_RE.sub("", text)
    stripped = PLACEHOLDER_RE.sub("", stripped)
    return stripped.strip() != ""


def _field_value(section: str | None, field: str) -> str | None:
    """Return the value of a `- Field:` bullet within a section body."""
    if not section:
        return None
    # [ \t] keeps the match on one line so an empty "- Field:" cannot swallow
    # the following line (a newline is whitespace that \s* would cross).
    pattern = re.compile(
        rf"^[ \t]*-[ \t]*{re.escape(field)}[ \t]*:[ \t]*(.*)$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(section)
    if not match:
        return None
    return match.group(1).strip()


def _is_blank_or_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    return PLACEHOLDER_RE.sub("", value).strip() == ""


def _first_token(value: str) -> str:
    return re.split(r"[\s,]+", value.strip(), maxsplit=1)[0].lower()


def _find_unresolved_placeholders(packet: str) -> list[str]:
    without_code = INLINE_CODE_RE.sub("", packet)
    return PLACEHOLDER_RE.findall(without_code)


def _docker_is_complete(value: str | None) -> tuple[bool, str | None]:
    """Return (ok, error). Accepts not-applicable, unavailable, or concrete text."""
    if _is_blank_or_placeholder(value):
        return False, None  # Caller reports the "missing" message.
    lowered = value.lower()
    if lowered == "not applicable" or lowered.startswith("unavailable"):
        return True, None
    # A concrete Docker strategy/command should name more than a bare word.
    if len(value.split()) < 2:
        return False, "incomplete"
    return True, None


def validate(packet: str) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    kind = detect_kind(packet)
    if kind is None:
        return None, [
            "packet must start with '# Codex plan review' or "
            "'# Codex milestone review'"
        ]

    if len(packet) > MAX_CHARACTERS:
        errors.append(
            f"packet has {len(packet)} characters; maximum is {MAX_CHARACTERS}"
        )

    # Required headings present and ordered.
    positions: list[int] = []
    schema_kind = "milestone_legacy" if kind == "milestone_legacy" else kind
    for heading in PACKET_SCHEMAS[schema_kind]:
        position = packet.find(heading)
        positions.append(position)
        if position < 0:
            errors.append(f"missing required heading: {heading}")
    present_positions = [position for position in positions if position >= 0]
    if present_positions != sorted(present_positions):
        errors.append("required headings are out of order")

    # Unresolved angle-bracket placeholders anywhere in the packet.
    placeholders = _find_unresolved_placeholders(packet)
    if placeholders:
        sample = ", ".join(placeholders[:3])
        errors.append(f"unresolved angle-bracket placeholder(s): {sample}")

    # No empty required sections. The title and Directed questions are exempt:
    # the title carries no body, and Directed questions may legitimately read "None."
    for heading in PACKET_SCHEMAS[schema_kind]:
        if heading.startswith("# ") or heading == "## Directed questions":
            continue
        body = section_body(packet, heading)
        if body is not None and not _has_real_content(body):
            errors.append(f"required section is empty: {heading}")

    # Directed questions: 0 to 3 allowed. Zero is valid ("None." or empty body).
    question_count = len(QUESTION_RE.findall(directed_questions_section(packet)))
    if question_count > 3:
        errors.append(
            f"directed questions contains {question_count} items; maximum is 3"
        )

    if kind == "plan":
        _validate_plan(packet, errors)
    else:
        _validate_milestone(packet, errors)

    return kind, errors


def _validate_plan(packet: str, errors: list[str]) -> None:
    evidence = section_body(packet, "## Evidence strategy")
    docker = _field_value(evidence, "Docker strategy")
    if _is_blank_or_placeholder(docker):
        errors.append(
            "missing or blank Docker strategy declaration under Evidence strategy"
        )
    else:
        ok, state = _docker_is_complete(docker)
        if not ok and state == "incomplete":
            errors.append(
                "Docker strategy declaration is incomplete; state 'not applicable', "
                "the container checks and final command, or 'unavailable — <reason>'"
            )


def _validate_milestone(packet: str, errors: list[str]) -> None:
    if packet.startswith("# Milestone review"):
        required = ("Milestone", "Baseline", "Scope", "Contract", "Non-goals", "Verification", "Evidence gaps")
        for field in required:
            value = _flat_field_value(packet, field)
            if _is_blank_or_placeholder(value):
                errors.append(f"missing or blank milestone field: {field}")
        return
    identity = section_body(packet, "## Identity")

    baseline = _field_value(identity, "Baseline")
    if _is_blank_or_placeholder(baseline):
        errors.append("missing or blank milestone Baseline under Identity")

    diff_mode = _field_value(identity, "Diff mode")
    if _is_blank_or_placeholder(diff_mode):
        errors.append("missing Diff mode under Identity")
    elif not any(mode in diff_mode for mode in VALID_DIFF_MODES):
        errors.append(
            f"invalid Diff mode '{diff_mode}'; expected one of: "
            f"{', '.join(VALID_DIFF_MODES)}"
        )

    evidence = section_body(packet, "## Evidence status")

    acceptance = _field_value(evidence, "Acceptance criteria")
    if _is_blank_or_placeholder(acceptance):
        errors.append("missing Acceptance criteria status under Evidence status")
    elif _first_token(acceptance) not in VALID_ACCEPTANCE_STATUSES:
        errors.append(
            f"invalid acceptance-evidence status '{acceptance}'; expected one of: "
            f"{', '.join(sorted(VALID_ACCEPTANCE_STATUSES))}"
        )

    real_boundaries = _field_value(evidence, "Real boundaries")
    if _is_blank_or_placeholder(real_boundaries):
        errors.append(
            "missing or blank Real boundaries declaration under Evidence status"
        )

    docker = _field_value(evidence, "Docker")
    if _is_blank_or_placeholder(docker):
        errors.append(
            "missing Docker applicability/evidence declaration under Evidence status"
        )
    else:
        ok, state = _docker_is_complete(docker)
        if not ok and state == "incomplete":
            errors.append(
                "Docker declaration is incomplete; state 'not applicable', the "
                "containerized command and services, or 'unavailable — <reason>'"
            )


def _flat_field_value(packet: str, field: str) -> str | None:
    """Return a flat packet field up to the next top-level field."""
    pattern = re.compile(
        rf"^[ \t]*{re.escape(field)}[ \t]*:[ \t]*(.*?)(?=^\w[\w -]*[ \t]*:|\Z)",
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(packet)
    return match.group(1).strip() if match else None


def _init_packet(args: argparse.Namespace) -> int:
    if args.kind == "milestone":
        scope = "\n".join(f"- {path}" for path in args.scope) or "- TODO: add path:symbol"
        packet = f"""# Milestone review

Milestone: {args.milestone}
Baseline: {args.baseline}
Scope:
{scope}

Contract:
- TODO: observable outcome

Non-goals:
- TODO: scope exclusion

Verification:
- TODO: exact command — result

Evidence gaps:
- TODO: none or exact gap

Prior findings:
- none

Directed questions:
- none
"""
    else:
        packet = """# Codex plan review

## Objective
TODO: concise objective

## Authority
- Specification: TODO: authoritative path or user contract
- Allowed delivery actions: TODO: boundaries
- Human gates: TODO: decisions returned to the user

## Global invariants
- TODO: invariant

## Milestones
### M1: TODO
- Contract: TODO
- Acceptance: TODO
- Dependencies: none
- Non-goals: TODO

## Integration strategy
- TODO

## Evidence strategy
- Acceptance ledger: TODO
- Required verification tiers: TODO
- Docker strategy: not applicable

## Directed questions
None.

## Known uncertainties
- TODO
"""
    if args.output:
        args.output.write_text(packet, encoding="utf-8")
    else:
        print(packet, end="")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init = argparse.ArgumentParser(
            prog=f"{sys.argv[0]} init", description="generate a compact packet scaffold"
        )
        init.add_argument("--kind", choices=("plan", "milestone"), required=True)
        init.add_argument("--milestone", default="M1")
        init.add_argument("--baseline", default="HEAD")
        init.add_argument("--scope", nargs="+", default=[])
        init.add_argument("--output", type=Path)
        return _init_packet(init.parse_args(sys.argv[2:]))

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="path to the Markdown review packet")
    args = parser.parse_args()

    try:
        packet = args.packet.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        parser.error(str(exc))

    kind, errors = validate(packet)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    assert kind is not None
    question_count = len(QUESTION_RE.findall(directed_questions_section(packet)))
    noun = "question" if question_count == 1 else "questions"
    print(
        f"OK: compact {kind} review packet "
        f"({len(packet)} characters, {question_count} directed {noun})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
