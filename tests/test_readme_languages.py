"""The README is split by language: README.md (English) and README_RU.md (Russian).

One file with both halves meant the language switcher was an anchor inside the
same document, the two versions shared a single table of contents, and GitHub
showed a Russian reader an English page first. These tests pin the split:

* both files exist and each links to the other (the link is what GitHub renders
  as the language switch, and npm rewrites relative links to the repository);
* the halves do not duplicate sections and keep the ones a reader needs;
* the combined heading is gone, so nothing links to a dead anchor;
* both files ship in the npm package, so the published page has the pair too.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EN = "README.md"
RU = "README_RU.md"

#: Sections every half must keep - the entry point and the license.
EN_REQUIRED = ("## 💡 The idea", "## 📄 License")
RU_REQUIRED = ("## 💡 Идея", "## 📄 Лицензия")


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8", errors="replace")


def test_both_language_files_exist():
    assert (REPO / EN).is_file()
    assert (REPO / RU).is_file()


def test_each_file_links_to_the_other_language():
    english = _read(EN)
    russian = _read(RU)
    assert "[🇷🇺 Русская версия](README_RU.md)" in english, (
        "the English README must link to README_RU.md for GitHub and npm")
    assert "](README.md)" in russian, (
        "the Russian README must link back to README.md")
    # The switch belongs near the top, where a reader looks for it.
    assert english.index("README_RU.md") < 1000
    assert russian.index("](README.md)") < 1000


def test_the_titles_name_the_project_in_their_own_language():
    english_first = _read(EN).splitlines()[0]
    russian_first = _read(RU).splitlines()[0]
    assert english_first.startswith("# ") and "Remote Harness MCP" in english_first
    assert russian_first.startswith("# ") and "Remote Harness MCP" in russian_first
    assert english_first != russian_first


def test_the_combined_russian_heading_is_gone():
    """No file may keep the old in-document heading, or its anchor is dead."""
    for relative in (EN, RU):
        assert "# 🇷🇺 Русская версия" not in _read(relative), relative
        assert "](#-русская-версия)" not in _read(relative), relative


def test_the_halves_do_not_duplicate_sections():
    english = re.findall(r"^## (.+)$", _read(EN), re.M)
    russian = re.findall(r"^## (.+)$", _read(RU), re.M)
    assert len(english) > 5 and len(russian) > 5, (english, russian)
    assert set(english).isdisjoint(set(russian)), (
        "the same section appears in both language files")


def test_each_half_keeps_its_entry_point_and_license():
    english = _read(EN)
    russian = _read(RU)
    for heading in EN_REQUIRED:
        assert heading in english, heading
    for heading in RU_REQUIRED:
        assert heading in russian, heading


def test_both_halves_show_the_setup_executable():
    """The copy/paste examples are what a reader follows; both languages need them."""
    for relative in (EN, RU):
        assert "RemoteHarnessMCP-Setup-" in _read(relative), relative


def test_both_files_ship_in_the_npm_package():
    files = json.loads(_read("package.json"))["files"]
    assert EN in files
    assert RU in files


def test_the_russian_file_is_actually_russian():
    russian = _read(RU)
    assert len(re.findall(r"[А-Яа-я]", russian)) > 2000, "README_RU.md lost its Russian text"
