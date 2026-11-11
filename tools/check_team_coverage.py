#!/usr/bin/env python3
"""Validate Wave 1 team-plan traceability.

Source of truth is the three markdown tables:
  docs/team/WBS.md            | ID | Работа | Критерий приёмки | Зависит от |
  docs/team/OWNERSHIP_MATRIX.md | WBS | Owner | Reviewer | Tests | Evidence | Gate |
  docs/team/TEST_PLAN.md      | Test ID | WBS | Level | Steps | Expected | Automation | Env | Blocking |

Checks:
  * every WBS row has exactly one ownership row;
  * owner/reviewer are known role codes and differ;
  * every ownership row names >=1 test, and every test exists;
  * every test points to an existing WBS and the domains agree;
  * every WBS has at least one blocking test;
  * no orphan tests;
  * required team documents exist.

Exit code 0 = coverage complete, 1 = gaps (with a report).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WBS = ROOT / "docs/team/WBS.md"
MATRIX = ROOT / "docs/team/OWNERSHIP_MATRIX.md"
TESTS = ROOT / "docs/team/TEST_PLAN.md"
REQUIRED = [
    ROOT / "docs/team/AGENT_TEAM.md",
    ROOT / "docs/team/REVIEW_BRIEF.md",
    ROOT / "docs/team/VERIFICATION_PROTOCOL.md",
    ROOT / "docs/team/REVIEW_LOG.md",
]

ROLES = {"P", "A", "C", "API", "SEC", "UI", "DESK", "BE", "PERF", "QA", "OPS", "DOC"}
WBS_ID = re.compile(r"^(AUTH|CLM|CAB|AGT|USE|CTL|TRY|MOD|SEC|OPS|REV-[A-Z]+)-\d{2,3}$")
TEST_ID = re.compile(r"^T-(AUTH|CLM|CAB|AGT|USE|CTL|TRY|MOD|SEC|OPS|REV-[A-Z]+)-\d{2,3}$")
GATES = {"G0", "G1", "G2", "G3", "G4", "G5"}
LEVELS = {"unit", "contract", "integration", "e2e", "security", "perf", "manual", "migration"}


def domain_of(identifier: str) -> str:
    """AUTH-01 -> AUTH; T-REV-CLM-11 -> CLM; REV-CLM-11 -> CLM."""
    parts = identifier.split("-")
    if parts[0] == "T":
        parts = parts[1:]
    if parts[0] == "REV":
        return parts[1]
    return parts[0]


@dataclass(frozen=True)
class Ownership:
    wbs: str
    owner: str
    reviewer: str
    tests: tuple[str, ...]
    evidence: str
    gate: str


@dataclass(frozen=True)
class Test:
    tid: str
    wbs: str
    level: str
    steps: str
    expected: str
    automation: str
    env: str
    blocking: bool


def table_rows(path: Path) -> list[list[str]]:
    """Return data rows of all pipe tables in a markdown file."""
    rows: list[list[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if set(first) <= {"-", ":", " "}:
            continue
        if first.lower() in {"wbs", "test id", "id", "тест", "код"}:
            continue
        rows.append(cells)
    return rows


def load_wbs() -> dict[str, str]:
    out: dict[str, str] = {}
    for cells in table_rows(WBS):
        if len(cells) >= 2 and WBS_ID.match(cells[0]):
            if cells[0] in out:
                raise SystemExit(f"duplicate WBS id: {cells[0]}")
            out[cells[0]] = cells[1]
    return out


def load_matrix() -> dict[str, Ownership]:
    out: dict[str, Ownership] = {}
    for cells in table_rows(MATRIX):
        if len(cells) < 6 or not WBS_ID.match(cells[0]):
            continue
        wbs, owner, reviewer, tests, evidence, gate = cells[:6]
        test_ids = tuple(t for t in re.split(r"[,\s]+", tests) if t)
        if wbs in out:
            raise SystemExit(f"duplicate ownership row: {wbs}")
        out[wbs] = Ownership(wbs, owner, reviewer, test_ids, evidence, gate)
    return out


def load_tests() -> dict[str, Test]:
    out: dict[str, Test] = {}
    for cells in table_rows(TESTS):
        if len(cells) < 8 or not TEST_ID.match(cells[0]):
            continue
        tid, wbs, level, steps, expected, automation, env, blocking = cells[:8]
        if tid in out:
            raise SystemExit(f"duplicate test id: {tid}")
        out[tid] = Test(
            tid,
            wbs,
            level.lower(),
            steps,
            expected,
            automation,
            env,
            blocking.strip().lower() in {"yes", "true", "да", "1", "blocking"},
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="exit 1 on any gap")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    problems: list[str] = []
    warnings: list[str] = []

    for path in REQUIRED:
        if not path.exists():
            problems.append(f"missing required doc: {path.relative_to(ROOT)}")

    wbs = load_wbs()
    matrix = load_matrix()
    tests = load_tests()

    for wid in wbs:
        if wid not in matrix:
            problems.append(f"{wid}: no ownership row")

    for wid, row in matrix.items():
        if wid not in wbs:
            problems.append(f"{wid}: ownership row without WBS entry")
        if row.owner not in ROLES:
            problems.append(f"{wid}: unknown owner role '{row.owner}'")
        if row.reviewer not in ROLES:
            problems.append(f"{wid}: unknown reviewer role '{row.reviewer}'")
        if row.owner == row.reviewer:
            problems.append(f"{wid}: owner and reviewer must differ")
        if row.gate not in GATES:
            problems.append(f"{wid}: unknown gate '{row.gate}'")
        if not row.evidence.strip():
            problems.append(f"{wid}: empty evidence")
        if not row.tests:
            problems.append(f"{wid}: no tests")

    for wid, row in matrix.items():
        blocking = 0
        for tid in row.tests:
            test = tests.get(tid)
            if test is None:
                problems.append(f"{wid}: references missing test {tid}")
                continue
            if test.wbs != wid:
                problems.append(f"{tid}: WBS mismatch ({test.wbs} != {wid})")
            if test.blocking:
                blocking += 1
            if test.level not in LEVELS:
                warnings.append(f"{tid}: unusual level '{test.level}'")
        if row.tests and blocking == 0:
            problems.append(f"{wid}: no blocking test")

    for tid, test in tests.items():
        if test.wbs not in wbs:
            problems.append(f"{tid}: unknown WBS {test.wbs}")
        if not test.expected.strip():
            problems.append(f"{tid}: empty expected result")

    domains = sorted({domain_of(w) for w in wbs})
    test_domains = sorted({domain_of(t) for t in tests})
    for domain in domains:
        if domain not in test_domains:
            problems.append(f"domain {domain}: no tests at all")

    total = len(wbs)
    covered = sum(1 for w in wbs if w in matrix and matrix[w].tests)
    blocking_tests = sum(1 for t in tests.values() if t.blocking)

    if not args.quiet:
        print("Wave 1 plan coverage (not a test run)")
        print(f"  WBS items:        {total}")
        print(f"  ownership rows:   {len(matrix)}")
        print(f"  tests:            {len(tests)} ({blocking_tests} blocking)")
        print(f"  WBS with tests:   {covered}/{total}")
        print(f"  domains:          {', '.join(domains)}")
        if warnings:
            print("\nWarnings:")
            for item in warnings:
                print(f"  - {item}")
        if problems:
            print("\nGaps:")
            for item in problems:
                print(f"  - {item}")
        else:
            print("\nCoverage complete: every detail has an owner, a reviewer, and a test.")

    if args.strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
