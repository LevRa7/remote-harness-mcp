"""Contract tests for tools/build-deb.py (universal Architecture: all .deb)."""

from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_deb", REPO / "tools" / "build-deb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_ar(data: bytes):
    assert data.startswith(b"!<arch>\n")
    pos = 8
    members = {}
    while pos < len(data):
        header = data[pos : pos + 60]
        assert len(header) == 60
        name = header[:16].decode("ascii").strip()
        size = int(header[48:58].decode("ascii").strip())
        pos += 60
        members[name] = data[pos : pos + size]
        pos += size + (size % 2)
    return members


def test_to_deb_version_converts_prerelease_hyphen():
    mod = _load_builder()
    assert mod.to_deb_version("0.7.2") == "0.7.2"
    assert mod.to_deb_version("0.7.0-rc1-alpha") == "0.7.0~rc1-alpha"


def test_build_deb_produces_valid_universal_archive(tmp_path):
    mod = _load_builder()
    out = tmp_path / "remote-harness-mcp_test_all.deb"
    built, sha, count = mod.build(out, root=REPO)

    assert built.is_file()
    assert len(sha) == 64
    assert count > 30

    members = _parse_ar(built.read_bytes())
    assert list(members.keys()) == ["debian-binary", "control.tar.gz", "data.tar.gz"]
    assert members["debian-binary"] == b"2.0\n"

    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as ctf:
        control = ctf.extractfile("./control").read().decode("utf-8")
        assert "Package: remote-harness-mcp\n" in control
        assert "Architecture: all\n" in control
        assert f"Version: {mod.to_deb_version(mod.version_from_module(REPO))}\n" in control
        assert "Depends: python3 (>= 3.9)\n" in control

    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as dtf:
        names = set(dtf.getnames())
        assert "./usr/bin/remote-harness-mcp" in names
        assert "./usr/bin/rhm" in names
        assert "./opt/remote-harness-mcp/core/server.py" in names
        assert "./opt/remote-harness-mcp/core/version.py" in names
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        assert dtf.getmember("./usr/bin/remote-harness-mcp").mode == 0o755
        assert dtf.getmember("./usr/bin/rhm").mode == 0o755
        # dpkg requires explicit directory entries before files inside them
        seen_dirs = set()
        for m in dtf.getmembers():
            if m.isdir():
                seen_dirs.add(m.name.rstrip("/"))
            else:
                parent = m.name.rsplit("/", 1)[0]
                assert parent in seen_dirs, f"missing parent dir {parent} before {m.name}"

    if shutil.which("dpkg-deb"):
        info = subprocess.run(["dpkg-deb", "--info", str(built)], capture_output=True, text=True, check=True)
        assert "Architecture: all" in info.stdout
        assert "Package: remote-harness-mcp" in info.stdout
