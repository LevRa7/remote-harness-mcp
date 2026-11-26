"""Unit tests for core/fs_index.py (SQLite FTS5 file index)."""

from __future__ import annotations

import os

from core import fs_index, mcp_tools


def test_file_index_refresh_and_queries(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "resume_2026.pdf").write_text("pdf", encoding="utf-8")
    (root / "notes.txt").write_text("txt", encoding="utf-8")
    sub = root / "projects" / "mesh"
    sub.mkdir(parents=True)
    (sub / "server.py").write_text("print(1)\n", encoding="utf-8")
    (sub / "Resume_Final.docx").write_text("doc", encoding="utf-8")

    # Ignored directory should be pruned
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "resume_ignored.pdf").write_text("skip", encoding="utf-8")

    db_path = str(tmp_path / "idx.db")
    idx = fs_index.FileIndex(db_path=db_path)

    stats = idx.refresh_root(str(root))
    assert stats["ok"] is True
    assert stats["file_count"] == 4
    assert stats["pruned_dirs"] >= 1
    assert idx.is_covered(str(root)) is True
    assert idx.is_covered(str(sub)) is True

    # Basename glob across whole root (case-insensitive on Windows)
    res = idx.query(str(root), "*resume*")
    assert res is not None
    assert res["count"] == 2
    assert res["backend"] in ("sqlite_fts5", "sqlite")
    names = sorted(os.path.basename(p) for p in res["files"])
    assert names == ["Resume_Final.docx", "resume_2026.pdf"]

    # Relative ** glob
    py_res = idx.query(str(root), "**/*.py")
    assert py_res is not None
    assert py_res["count"] == 1
    assert py_res["files"][0].endswith("server.py")

    # max_depth=0 only returns root-level files
    shallow = idx.query(str(root), "*resume*", max_depth=0)
    assert shallow is not None
    assert [os.path.basename(p) for p in shallow["files"]] == ["resume_2026.pdf"]

    # Subdirectory query only returns files under that subdirectory
    sub_res = idx.query(str(sub), "*resume*")
    assert sub_res is not None
    assert [os.path.basename(p) for p in sub_res["files"]] == ["Resume_Final.docx"]


def test_glob_find_uses_index_when_covered(tmp_path, monkeypatch):
    root = tmp_path / "indexed_root"
    root.mkdir()
    (root / "target_cv.pdf").write_text("cv", encoding="utf-8")

    idx = fs_index.FileIndex(db_path=str(tmp_path / "mcp_idx.db"))
    idx.refresh_root(str(root))
    monkeypatch.setattr(fs_index, "default_index", lambda: idx)

    mcp_tools.configure(workspace=str(root), read_only=False)
    res = mcp_tools.call_tool("glob_find", {"path": str(root), "pattern": "*cv*", "use_index": "only"})
    assert res["count"] == 1
    assert res["files"][0].endswith("target_cv.pdf")
    assert res["backend"] in ("sqlite_fts5", "sqlite")

    # Uncovered path with use_index="only" reports indexed=False cleanly
    other = tmp_path / "other"
    other.mkdir()
    miss = mcp_tools.call_tool("glob_find", {"path": str(other), "pattern": "*.txt", "use_index": "only"})
    assert miss["count"] == 0
    assert miss["indexed"] is False
