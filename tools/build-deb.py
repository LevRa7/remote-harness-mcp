#!/usr/bin/env python3
"""tools/build-deb.py - build a universal Architecture: all Debian (.deb) package.

Produces ``dist/remote-harness-mcp_<version>_all.deb`` plus a ``.sha256`` file
using only the Python standard library (so it works identically on Linux, macOS,
and Windows without requiring ``dpkg-deb`` at build time).

The resulting package installs cleanly on Debian, Ubuntu, Raspberry Pi OS, and
any APT-based Linux distribution across x64, arm64, x86, and armhf:

    sudo apt install ./remote-harness-mcp_<version>_all.deb
    remote-harness-mcp --folder ./project --no-shell --quick-tunnel
    rhm --help
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path
from typing import Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent

PAYLOAD_DIRS = ("core", "skills", "ops")
PAYLOAD_FILES = ("package.json", "VERSION", "LICENSE", "README.md", "README_RU.md", "install.sh")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".state", "node_modules", ".git"}
SKIP_SUFFIXES = (".pyc", ".pyo")
TEXT_LF_SUFFIXES = (".py", ".sh", ".md", ".json", ".txt", ".conf", ".template", ".yml", ".yaml")
INSTALL_PREFIX = "opt/remote-harness-mcp"
FIXED_MTIME = 1700000000


def version_from_module(root: Path = REPO) -> str:
    path = root / "core" / "version.py"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("core/version.py declares no __version__")


def to_deb_version(ver: str) -> str:
    """Convert SemVer pre-release '-' to Debian '~' so 0.7.0~rc1 < 0.7.0."""
    if "-" in ver:
        base, pre = ver.split("-", 1)
        return f"{base}~{pre}"
    return ver


def _iter_payload(root: Path) -> Iterable[Tuple[Path, str]]:
    for rel in list(PAYLOAD_DIRS) + list(PAYLOAD_FILES):
        base = root / rel
        if base.is_file():
            yield base, rel.replace("\\", "/")
            continue
        if not base.is_dir():
            raise SystemExit(f"payload item is missing: {base}")
        for current, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if name.endswith(SKIP_SUFFIXES) or name.startswith("."):
                    continue
                abs_p = Path(current) / name
                rel_p = abs_p.relative_to(root).as_posix()
                yield abs_p, rel_p


def _read_normalized(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_LF_SUFFIXES or path.name in ("VERSION", "LICENSE"):
        data = data.replace(b"\r\n", b"\n")
    return data


def _add_tar_dir(tf: tarfile.TarFile, arcname: str, mode: int = 0o755) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.type = tarfile.DIRTYPE
    info.size = 0
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = FIXED_MTIME
    tf.addfile(info)


def _ensure_parent_dirs(tf: tarfile.TarFile, seen: set, rel_path: str) -> None:
    if "./" not in seen:
        _add_tar_dir(tf, "./")
        seen.add("./")
    parts = rel_path.strip("/").split("/")[:-1]
    cur = "."
    for part in parts:
        cur = f"{cur}/{part}"
        dname = cur + "/"
        if dname not in seen:
            _add_tar_dir(tf, dname)
            seen.add(dname)


def _add_tar_bytes(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = FIXED_MTIME
    tf.addfile(info, io.BytesIO(data))


def _ar_member(name: str, payload: bytes) -> bytes:
    header = (
        f"{name:<16}"
        f"{FIXED_MTIME:<12}"
        f"{0:<6}"
        f"{0:<6}"
        f"{100644:<8}"
        f"{len(payload):<10}"
        "`\n"
    ).encode("ascii")
    assert len(header) == 60
    pad = b"\n" if (len(payload) % 2 == 1) else b""
    return header + payload + pad


def _build_data_tar(root: Path) -> Tuple[bytes, int, List[str]]:
    buf = io.BytesIO()
    total_bytes = 0
    md5_lines: List[str] = []

    seen_dirs: set = set()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        # 1. Application payload under /opt/remote-harness-mcp
        for abs_p, rel_p in _iter_payload(root):
            data = _read_normalized(abs_p)
            dest = f"{INSTALL_PREFIX}/{rel_p}"
            _ensure_parent_dirs(tf, seen_dirs, dest)
            mode = 0o755 if rel_p.endswith(".sh") else 0o644
            _add_tar_bytes(tf, f"./{dest}", data, mode=mode)
            total_bytes += len(data)
            md5_lines.append(f"{hashlib.md5(data).hexdigest()}  {dest}")

        # 2. /usr/bin/remote-harness-mcp (standalone server CLI)
        cli_sh = (
            "#!/bin/sh\n"
            "set -e\n"
            'export PYTHONPATH="/opt/remote-harness-mcp${PYTHONPATH:+:$PYTHONPATH}"\n'
            'exec python3 -m core.server "$@"\n'
        ).encode("utf-8")
        _ensure_parent_dirs(tf, seen_dirs, "usr/bin/remote-harness-mcp")
        _add_tar_bytes(tf, "./usr/bin/remote-harness-mcp", cli_sh, mode=0o755)
        total_bytes += len(cli_sh)
        md5_lines.append(f"{hashlib.md5(cli_sh).hexdigest()}  usr/bin/remote-harness-mcp")

        # 3. /usr/bin/rhm (interactive console menu)
        rhm_sh = (
            "#!/bin/sh\n"
            "set -e\n"
            'cd /opt/remote-harness-mcp\n'
            'exec python3 -m core.menu "$@"\n'
        ).encode("utf-8")
        _ensure_parent_dirs(tf, seen_dirs, "usr/bin/rhm")
        _add_tar_bytes(tf, "./usr/bin/rhm", rhm_sh, mode=0o755)
        total_bytes += len(rhm_sh)
        md5_lines.append(f"{hashlib.md5(rhm_sh).hexdigest()}  usr/bin/rhm")

    installed_kb = max(1, (total_bytes + 1023) // 1024)
    return buf.getvalue(), installed_kb, md5_lines


def _build_control_tar(deb_version: str, installed_kb: int, md5_lines: List[str]) -> bytes:
    control = (
        f"Package: remote-harness-mcp\n"
        f"Version: {deb_version}\n"
        f"Section: utils\n"
        f"Priority: optional\n"
        f"Architecture: all\n"
        f"Depends: python3 (>= 3.9)\n"
        f"Recommends: git\n"
        f"Installed-Size: {installed_kb}\n"
        f"Maintainer: LevRa7 <https://github.com/LevRa7/remote-harness-mcp>\n"
        f"Homepage: https://github.com/LevRa7/remote-harness-mcp\n"
        f"Description: Remote Harness MCP - control any PC or server via MCP\n"
        f" Open-source Model Context Protocol (MCP) server for Gemini, ChatGPT,\n"
        f" and Claude (App & Web, including free accounts). Pure Python stdlib\n"
        f" architecture-independent package supporting x64, arm64, x86, and armhf.\n"
    ).encode("utf-8")

    md5sums = ("\n".join(md5_lines) + "\n").encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        _add_tar_bytes(tf, "./control", control, mode=0o644)
        _add_tar_bytes(tf, "./md5sums", md5sums, mode=0o644)
    return buf.getvalue()


def build(out_path: Path, *, root: Path = REPO, version: str = "") -> Tuple[Path, str, int]:
    ver = version or version_from_module(root)
    deb_ver = to_deb_version(ver)
    data_tar = _build_data_tar(root)
    data_bytes, installed_kb, md5_lines = data_tar
    control_bytes = _build_control_tar(deb_ver, installed_kb, md5_lines)

    ar_bytes = (
        b"!<arch>\n"
        + _ar_member("debian-binary", b"2.0\n")
        + _ar_member("control.tar.gz", control_bytes)
        + _ar_member("data.tar.gz", data_bytes)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(ar_bytes)
    sha = hashlib.sha256(ar_bytes).hexdigest()
    return out_path, sha, len(md5_lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build universal Architecture: all .deb package.")
    parser.add_argument("--out", default="", help="output .deb path (default dist/remote-harness-mcp_<ver>_all.deb)")
    parser.add_argument("--root", default=str(REPO), help="repository root")
    parser.add_argument("--version", default="", help="override version string")
    parser.add_argument("--no-checksum", action="store_true", help="skip writing .sha256")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    ver = args.version or version_from_module(root)
    deb_ver = to_deb_version(ver)
    out = Path(args.out) if args.out else (root / "dist" / f"remote-harness-mcp_{deb_ver}_all.deb")

    out, sha, count = build(out, root=root, version=ver)
    if not args.no_checksum:
        checksum = Path(str(out) + ".sha256")
        checksum.write_text(f"{sha}  {out.name}\n", encoding="ascii", newline="\n")

    print(f"deb     : {out}")
    print(f"arch    : all")
    print(f"version : {deb_ver}")
    print(f"files   : {count}")
    print(f"sha256  : {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
