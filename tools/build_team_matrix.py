#!/usr/bin/env python3
"""Generate the Wave 1 ownership matrix and test-plan skeleton from WBS.md.

The generated files are the baseline; reviewers refine wording and add
domain-specific cases. IDs and 1:1 coverage are mechanical so no detail can
silently disappear.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WBS = ROOT / "docs/team/WBS.md"
MATRIX = ROOT / "docs/team/OWNERSHIP_MATRIX.md"
TESTS = ROOT / "docs/team/TEST_PLAN.md"

# domain -> (owner, reviewer, level, automation, env, gate)
DOMAIN = {
    "AUTH": ("BE", "SEC", "unit", "pytest", "local + fake OIDC", "G3"),
    "CLM": ("BE", "SEC", "integration", "pytest", "local + fake OIDC", "G3"),
    "CAB": ("UI", "API", "e2e", "Playwright", "browser + seeded DB", "G3"),
    "AGT": ("BE", "SEC", "unit", "pytest", "local + fake OIDC", "G3"),
    "USE": ("BE", "QA", "unit", "pytest", "tmp fs", "G3"),
    "CTL": ("BE", "DESK", "integration", "pytest", "local node", "G3"),
    "TRY": ("DESK", "UI", "manual", "manual checklist", "Windows 11", "G3"),
    "MOD": ("BE", "SEC", "unit", "pytest", "fake gateway + node", "G3"),
    "SEC": ("SEC", "C", "security", "pytest + scanner", "local + CI", "G5"),
    "OPS": ("OPS", "QA", "migration", "pytest + shell", "staging VM", "G5"),
    "PERF": ("PERF", "BE", "perf", "benchmark + EXPLAIN", "2 vCPU/4 GB staging", "G3"),
    "QA": ("QA", "C", "e2e", "pytest + Playwright", "CI + staging", "G5"),
    "UI": ("UI", "QA", "e2e", "Playwright + manual", "browser + Windows 11", "G3"),
    "DOC": ("DOC", "QA", "manual", "manual review", "docs", "G3"),
    "ARC": ("A", "C", "manual", "review + import test", "repo", "G3"),
    "RESCUE": ("BE", "SEC", "unit", "pytest + fixtures", "Linux + fake sysfs", "G5"),
    "MENU": ("DESK", "UI", "e2e", "pytest + manual", "Windows + Linux terminal", "G3"),
    "SANDBOX": ("BE", "SEC", "integration", "pytest + docker", "Docker/Podman host", "G3"),
    "WEB": ("UI", "SEC", "e2e", "pytest + browser", "loopback browser", "G3"),
    "DRIVE": ("BE", "SEC", "integration", "pytest + google", "test Google account", "G3"),
}

BLOCKING_EXCEPTIONS: set[str] = set()  # every detail blocks its gate in Wave 1


WBS_RE = re.compile(r"^(AUTH|CLM|CAB|AGT|USE|CTL|TRY|MOD|SEC|OPS|REV-[A-Z]+)-\d{2,3}$")


def parse_wbs() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for raw in WBS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or not WBS_RE.match(cells[0]):
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def domain_of(wbs: str) -> str:
    if wbs.startswith("REV-"):
        return wbs.split("-")[1]
    return wbs.split("-")[0]


def test_id(wbs: str) -> str:
    return "T-" + wbs


def existing_tests() -> dict[str, str]:
    """Preserve refined rows written by the test author; only add new IDs."""
    out: dict[str, str] = {}
    if not TESTS.exists():
        return out
    for raw in TESTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 8 and cells[0].startswith("T-"):
            out[cells[0]] = line
    return out


def main() -> None:
    rows = parse_wbs()
    kept = existing_tests()
    matrix = [
        "# Матрица ответственности Wave 1",
        "",
        "> Сгенерировано из `WBS.md`; проверяется `tools/check_team_coverage.py --strict`.",
        "> Владелец делает, ревьюер независимо проверяет. Один и тот же роль-код в обеих колонках запрещён.",
        "",
        "| WBS | Owner | Reviewer | Tests | Evidence | Gate |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    tests = [
        "# Тест-план Wave 1",
        "",
        "> Один тест на каждую деталь WBS плюс доменные кейсы. `Blocking=yes` означает: без свежего зелёного прогона спринт не закрывается.",
        "> Шаги должны быть воспроизводимы на указанном окружении; доказательство — в `artifacts/<WBS>/`.",
        "",
        "| Test ID | WBS | Level | Steps | Expected | Automation | Env | Blocking |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for wbs, work, criteria in rows:
        domain = domain_of(wbs)
        owner, reviewer, level, automation, env, gate = DOMAIN[domain]
        tid = test_id(wbs)
        blocking = "no" if wbs in BLOCKING_EXCEPTIONS else "yes"
        steps = f"1) Подготовить окружение: {env}. 2) {work}. 3) Проверить: {criteria}."
        matrix.append(
            f"| {wbs} | {owner} | {reviewer} | {tid} | `artifacts/{wbs}/{tid}.log` | {gate} |"
        )
        tests.append(
            kept.get(
                tid,
                f"| {tid} | {wbs} | {level} | {steps} | {criteria} | {automation} | {env} | {blocking} |",
            )
        )

    MATRIX.write_text("\n".join(matrix) + "\n", encoding="utf-8")
    TESTS.write_text("\n".join(tests) + "\n", encoding="utf-8")
    print(f"generated {len(rows)} rows ({len(kept)} kept) -> {MATRIX.name}, {TESTS.name}")


if __name__ == "__main__":
    main()
