"""Contract tests keeping README.md and README_RU.md in sync with core/mcp_tools.py.

Pins Task 1.4 (tool count & key new tools/modes documented in both languages) and
Task 4.3 (Threat Model / Модель угроз section present in both files).
"""

import re
from pathlib import Path

from core import mcp_tools

REPO = Path(__file__).resolve().parent.parent
README_EN = REPO / "README.md"
README_RU = REPO / "README_RU.md"

KEY_NEW_TOOLS = ("git_status", "git_diff", "fetch_url")
KEY_MODES_AND_FEATURES = (
    "MESH_TOOLSETS",
    "--folder",
    "--no-shell",
    "--quick-tunnel",
    "OAuth 2.1",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_en_tool_count_matches_all_tools():
    text = _read(README_EN)
    expected = len(mcp_tools.ALL_TOOLS)
    match = re.search(r"\*\*(\d+)\s+tools on the node", text)
    assert match is not None, "README.md must state '**<N> tools on the node'"
    assert int(match.group(1)) == expected, (
        f"README.md claims {match.group(1)} tools, but len(ALL_TOOLS) == {expected}"
    )


def test_readme_ru_tool_count_matches_all_tools():
    text = _read(README_RU)
    expected = len(mcp_tools.ALL_TOOLS)
    match = re.search(r"\*\*(\d+)\s+инструмент\w*\s+на узле", text)
    assert match is not None, "README_RU.md must state '**<N> инструментов на узле'"
    assert int(match.group(1)) == expected, (
        f"README_RU.md claims {match.group(1)} tools, but len(ALL_TOOLS) == {expected}"
    )


def test_both_readmes_mention_key_new_tools_and_modes():
    for path in (README_EN, README_RU):
        text = _read(path)
        for token in KEY_NEW_TOOLS + KEY_MODES_AND_FEATURES:
            assert token in text, f"{path.name} is missing mention of {token!r}"


def test_both_readmes_include_threat_model_section():
    en_text = _read(README_EN)
    ru_text = _read(README_RU)
    assert re.search(r"^##\s+.*Threat Model\b", en_text, re.M), (
        "README.md is missing a Threat Model section"
    )
    assert re.search(r"^##\s+.*Модель угроз\b", ru_text, re.M), (
        "README_RU.md is missing a Модель угроз section"
    )
    # Threat model should appear near the top of the docs (first 15% of file).
    assert en_text.index("Threat Model") < len(en_text) * 0.2
    assert ru_text.index("Модель угроз") < len(ru_text) * 0.2
