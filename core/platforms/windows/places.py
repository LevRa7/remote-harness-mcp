"""core/platforms/windows/places.py - instant user-context snapshot (stdlib only).

Answers "where are the user's documents, recent files, and drives?" in a single
read-only call (<100 ms) without scanning disks:

* Known Folders via ``shell32.SHGetKnownFolderPath`` (or standard home paths off
  Windows);
* Recent files from ``%APPDATA%\\Microsoft\\Windows\\Recent`` using a pure-Python
  MS-SHLLINK (``.lnk``) binary parser (zero COM / pywin32 dependencies);
* Fixed drives overview with free/total space and top-level folders.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from core.platforms.windows import winapi

IS_WINDOWS = os.name == "nt"

# Standard Windows Known Folder GUIDs (KNOWNFOLDERID).
_KNOWN_FOLDER_IDS: Tuple[Tuple[str, str, str], ...] = (
    ("desktop", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop"),
    ("documents", "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents"),
    ("downloads", "374DE290-123F-4565-9164-39C4925E467B", "Downloads"),
    ("pictures", "33E28130-4E1E-4676-835A-98395C3BC3BB", "Pictures"),
    ("videos", "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "Videos"),
    ("music", "4BD8D571-6D19-48D3-BE97-422220080E43", "Music"),
    ("onedrive", "A52BBA46-E9E1-435F-B3D9-28DAA648C0F6", "OneDrive"),
)

_SKIP_TOP_LEVEL = frozenset((
    "$recycle.bin",
    "system volume information",
    "config.msi",
    "recovery",
    "perflogs",
    "msocache",
))

# MS-SHLLINK constants
_LNK_HEADER_SIZE = 0x4C
_LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")
_FLAG_HAS_LINK_TARGET_ID_LIST = 0x01
_FLAG_HAS_LINK_INFO = 0x02


def _known_folder_win32(guid_str: str) -> Optional[str]:
    if not (IS_WINDOWS and getattr(winapi, "SHGetKnownFolderPath", None)):
        return None
    try:
        guid = winapi.GUID.from_uuid(guid_str)
        ptr = wintypes.LPWSTR()
        hr = winapi.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            return None
        try:
            return str(ptr.value)
        finally:
            if getattr(winapi, "CoTaskMemFree", None):
                winapi.CoTaskMemFree(ptr)
    except Exception:
        return None


def known_folders() -> Dict[str, str]:
    """Return existing user folders (Desktop, Documents, Downloads, OneDrive, Telegram Desktop)."""
    home = os.path.expanduser("~")
    out: Dict[str, str] = {}
    if home and os.path.isdir(home):
        out["home"] = os.path.realpath(home)

    for key, guid_str, fallback_name in _KNOWN_FOLDER_IDS:
        path = _known_folder_win32(guid_str)
        if not path and home:
            candidate = os.path.join(home, fallback_name)
            if os.path.isdir(candidate):
                path = candidate
        if path and os.path.isdir(path):
            out[key] = os.path.realpath(path)

    # Extra high-signal locations on Windows
    if "onedrive" not in out:
        for env_key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            val = os.environ.get(env_key)
            if val and os.path.isdir(val):
                out["onedrive"] = os.path.realpath(val)
                break

    downloads = out.get("downloads")
    if downloads:
        tg_dir = os.path.join(downloads, "Telegram Desktop")
        if os.path.isdir(tg_dir):
            out["telegram_downloads"] = os.path.realpath(tg_dir)

    return out


def _read_cstr_mbcs(buf: bytes, offset: int) -> str:
    if offset <= 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\x00", offset)
    if end == -1:
        end = len(buf)
    raw = buf[offset:end]
    for enc in ("mbcs", "cp1251", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_cstr_utf16(buf: bytes, offset: int) -> str:
    if offset <= 0 or offset >= len(buf):
        return ""
    pos = offset
    end = len(buf) - 1
    while pos < end:
        if buf[pos] == 0 and buf[pos + 1] == 0:
            break
        pos += 2
    return buf[offset:pos].decode("utf-16-le", errors="replace")


def parse_lnk(path: str) -> Optional[str]:
    """Extract the local target path from a Windows ``.lnk`` file (MS-SHLLINK).

    Returns ``None`` if the file is not a valid Shell Link or has no local target
    (for example, a virtual Control Panel item). Never raises.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(65536)
    except Exception:
        return None

    if len(data) < _LNK_HEADER_SIZE:
        return None
    header_size = struct.unpack_from("<I", data, 0)[0]
    if header_size != _LNK_HEADER_SIZE:
        return None
    if data[4:20] != _LNK_CLSID:
        return None

    link_flags = struct.unpack_from("<I", data, 20)[0]
    pos = _LNK_HEADER_SIZE

    if link_flags & _FLAG_HAS_LINK_TARGET_ID_LIST:
        if pos + 2 > len(data):
            return None
        id_list_size = struct.unpack_from("<H", data, pos)[0]
        pos += 2 + id_list_size
        if pos > len(data):
            return None

    if not (link_flags & _FLAG_HAS_LINK_INFO):
        return None
    if pos + 28 > len(data):
        return None

    li_start = pos
    li_size, li_header_size, li_flags = struct.unpack_from("<III", data, li_start)
    if li_size < 28 or li_start + li_size > len(data):
        return None
    li_buf = data[li_start:li_start + li_size]

    # Bit 0 of LinkInfoFlags = VolumeIDAndLocalBasePath
    if not (li_flags & 0x01):
        return None

    local_base_off = struct.unpack_from("<I", li_buf, 16)[0]
    common_suffix_off = struct.unpack_from("<I", li_buf, 24)[0]

    target = ""
    if li_header_size >= 0x24 and len(li_buf) >= 36:
        unicode_base_off = struct.unpack_from("<I", li_buf, 28)[0]
        unicode_suffix_off = struct.unpack_from("<I", li_buf, 32)[0]
        if unicode_base_off:
            base_u = _read_cstr_utf16(li_buf, unicode_base_off)
            suffix_u = _read_cstr_utf16(li_buf, unicode_suffix_off) if unicode_suffix_off else ""
            target = (base_u + suffix_u).strip()

    if not target and local_base_off:
        base_a = _read_cstr_mbcs(li_buf, local_base_off)
        suffix_a = _read_cstr_mbcs(li_buf, common_suffix_off) if common_suffix_off else ""
        target = (base_a + suffix_a).strip()

    return target or None


def recent_files(
    limit: int = 30,
    recent_dir: Optional[str] = None,
    only_existing: bool = True,
) -> List[Dict[str, Any]]:
    """Return recently opened files from Windows Recent shortcuts, newest first."""
    limit = max(1, min(100, int(limit)))
    if recent_dir is None:
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return []
        recent_dir = os.path.join(appdata, "Microsoft", "Windows", "Recent")
    if not os.path.isdir(recent_dir):
        return []

    entries: List[Tuple[float, str, str]] = []
    try:
        with os.scandir(recent_dir) as it:
            for entry in it:
                if not entry.name.lower().endswith(".lnk"):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                entries.append((mtime, entry.name, entry.path))
    except OSError:
        return []

    entries.sort(key=lambda item: item[0], reverse=True)
    seen = set()
    results: List[Dict[str, Any]] = []
    for mtime, name, lnk_path in entries:
        target = parse_lnk(lnk_path)
        if not target:
            continue
        norm = target.lower() if IS_WINDOWS else target
        if norm in seen:
            continue
        exists = os.path.exists(target)
        if only_existing and not exists:
            continue
        seen.add(norm)
        results.append({
            "name": os.path.basename(target) or name[:-4],
            "target": target,
            "exists": exists,
            "is_dir": os.path.isdir(target) if exists else False,
            "mtime": round(mtime, 3),
        })
        if len(results) >= limit:
            break
    return results


def _top_level_dirs(root: str, limit: int) -> List[str]:
    out: List[str] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                if entry.name.lower() in _SKIP_TOP_LEVEL:
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        out.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda s: s.lower())
    return out[:limit]


def drives_overview(top_level_limit: int = 30) -> List[Dict[str, Any]]:
    """Return fixed drives (or POSIX root/home) with free space and 1st-level folders."""
    top_level_limit = max(0, min(100, int(top_level_limit)))
    drives: List[Dict[str, Any]] = []

    if IS_WINDOWS and getattr(winapi, "GetLogicalDrives", None):
        mask = int(winapi.GetLogicalDrives())
        for idx in range(26):
            if not (mask & (1 << idx)):
                continue
            letter = chr(ord("A") + idx)
            root = f"{letter}:\\"
            drive_type = int(winapi.GetDriveTypeW(root)) if getattr(winapi, "GetDriveTypeW", None) else 0
            # 3 = DRIVE_FIXED, 2 = DRIVE_REMOVABLE
            if drive_type not in (2, 3):
                continue
            total_b = ctypes.c_ulonglong(0)
            free_b = ctypes.c_ulonglong(0)
            avail_b = ctypes.c_ulonglong(0)
            total_gb = None
            free_gb = None
            if getattr(winapi, "GetDiskFreeSpaceExW", None):
                ok = winapi.GetDiskFreeSpaceExW(
                    root, ctypes.byref(avail_b), ctypes.byref(total_b), ctypes.byref(free_b)
                )
                if ok and total_b.value > 0:
                    total_gb = round(total_b.value / (1024 ** 3), 2)
                    free_gb = round(free_b.value / (1024 ** 3), 2)
            drives.append({
                "root": root,
                "type": "fixed" if drive_type == 3 else "removable",
                "total_gb": total_gb,
                "free_gb": free_gb,
                "top_dirs": _top_level_dirs(root, top_level_limit) if top_level_limit else [],
            })
        return drives

    # Cross-platform POSIX fallback
    home = os.path.expanduser("~")
    for root in ("/", home):
        if root and os.path.isdir(root):
            drives.append({
                "root": root,
                "type": "posix",
                "top_dirs": _top_level_dirs(root, top_level_limit) if top_level_limit else [],
            })
    return drives


def collect_places(
    recent_limit: int = 30,
    top_level_limit: int = 30,
    only_existing_recent: bool = True,
) -> Dict[str, Any]:
    """One-call snapshot of user folders, recent files, and fixed drives."""
    t0 = time.monotonic()
    folders = known_folders()
    recent = recent_files(limit=recent_limit, only_existing=only_existing_recent)
    drives = drives_overview(top_level_limit=top_level_limit)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "platform": sys.platform,
        "known_folders": folders,
        "recent": recent,
        "recent_count": len(recent),
        "drives": drives,
        "elapsed_ms": elapsed_ms,
        "note": "[VERIFIED BY TOOL] places come from the real host",
    }
