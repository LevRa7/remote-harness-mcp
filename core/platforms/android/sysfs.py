"""core/platforms/android/sysfs.py - Android facts from /sys, /proc and statvfs.

A phone exposes most of its own telemetry as files, and reading them is the
difference between a node that works on a clean Termux install and one that
needs a second APK.  Specifically:

* **battery** - ``/sys/class/power_supply/<name>/{capacity,status,voltage_now,
  current_now,temp}``; ``termux-battery-status`` is only the fallback for a
  vendor that hides those files;
* **thermal** - ``/sys/class/thermal/thermal_zone*/{type,temp}``;
* **storage** - ``statvfs`` of the Termux home and the shared volume;
* **network** - ``/proc/net/dev`` counters.

Everything is read-only and best effort: a missing file means "this device does
not publish it", which is a normal answer on Android, not an error.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.platforms import common

#: Where a phone keeps its sensors and power supplies.
POWER_SUPPLY = "/sys/class/power_supply"
THERMAL = "/sys/class/thermal"
SHARED_STORAGE = "/storage/emulated/0"


def _supplies(root: str = POWER_SUPPLY) -> List[str]:
    try:
        return sorted(os.listdir(root))
    except OSError:
        return []


def battery(root: str = POWER_SUPPLY) -> Optional[Dict[str, Any]]:
    """The main battery: percentage, charging state, voltage, current, temperature.

    A phone can expose several supplies (``battery``, ``usb``, ``ac``,
    ``wireless``); the one with a ``capacity`` file is the battery, and the rest
    only say whether something is plugged in.
    """
    best: Optional[Dict[str, Any]] = None
    for name in _supplies(root):
        base = os.path.join(root, name)
        capacity = common.read_int(os.path.join(base, "capacity"))
        if capacity is None:
            continue
        status = common.read_text(os.path.join(base, "status")).strip()
        temperature = common.read_int(os.path.join(base, "temp"))
        voltage = common.read_int(os.path.join(base, "voltage_now"))
        current = common.read_int(os.path.join(base, "current_now"))
        entry: Dict[str, Any] = {
            "available": True,
            "source": "sysfs:%s" % name,
            "percent": int(capacity),
            "status": status.lower() or "unknown",
            "charging": status.lower() in ("charging", "full"),
        }
        # temp is tenths of a degree; voltage is microvolts; current microamps.
        if temperature is not None:
            entry["temperature_c"] = round(temperature / 10.0, 1)
        if voltage is not None:
            entry["voltage_v"] = round(voltage / 1_000_000.0, 3)
        if current is not None:
            entry["current_a"] = round(current / 1_000_000.0, 3)
        if best is None or entry["percent"] > best.get("percent", -1):
            best = entry
    return best


def thermal(root: str = THERMAL, limit: int = 8) -> List[Dict[str, Any]]:
    """Named temperature zones, hottest first (the phone's CPU lives in one).

    Android exposes far more zones than a report should carry - a phone here has
    107 - and only some of them are temperatures:

    * most report **millidegrees** (``38300`` = 38.3 C);
    * a few report plain degrees;
    * level counters report small integers (``750``), which must never be read as
      750 C;
    * a disconnected sensor reports ``-273000`` (absolute zero), which is "no
      reading", not a temperature.

    The plausibility window is what separates them, and a zone outside it is
    skipped rather than shown as a fantasy value.
    """
    rows: List[Dict[str, Any]] = []
    try:
        zones = sorted(os.listdir(root))
    except OSError:
        return rows
    for zone in zones:
        if not zone.startswith("thermal_zone"):
            continue
        base = os.path.join(root, zone)
        raw = common.read_int(os.path.join(base, "temp"))
        if raw is None:
            continue
        name = common.read_text(os.path.join(base, "type")).strip() or zone
        celsius = raw / 1000.0 if abs(raw) > 1000 else float(raw)
        if not -50.0 <= celsius <= 150.0:
            continue  # a level counter or a disconnected sensor
        rows.append({"zone": zone, "name": name, "celsius": round(celsius, 1)})
    rows.sort(key=lambda item: item["celsius"], reverse=True)
    return rows[:limit]


def interfaces() -> Dict[str, Dict[str, int]]:
    """``{iface: {rx_bytes, tx_bytes}}`` from ``/proc/net/dev``."""
    result: Dict[str, Dict[str, int]] = {}
    for line in common.read_text("/proc/net/dev", 65536).splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if name.strip() in ("Inter-|", "face") or len(fields) < 9:
            continue
        try:
            result[name.strip()] = {
                "rx_bytes": int(fields[0]),
                "tx_bytes": int(fields[8]),
            }
        except (IndexError, ValueError):
            continue
    return result


def storage(paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Free/total space for the volumes a phone node actually uses."""
    rows: List[Dict[str, Any]] = []
    home = os.path.expanduser("~")
    candidates = paths or [home, SHARED_STORAGE, "/data"]
    seen = set()
    for path in candidates:
        if not path or path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        usage = common.disk_usage(path)
        if usage is None:
            continue
        total_gb, used_gb, free_gb = usage
        rows.append({
            "path": path,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": common.pct(used_gb, total_gb),
        })
    return rows


def soc_model() -> str:
    """The SoC/board name from ``/proc/device-tree/model`` when Android exposes it."""
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        text = common.read_text(path, 128).strip("\x00\n ")
        if text:
            return text
    return ""


def wake_lock_state() -> Optional[bool]:
    """Whether a Termux wake lock is held, when the bridge can tell."""
    try:
        from core.platforms.android import termux
    except Exception:  # pragma: no cover - partial checkout
        return None
    try:
        return termux.wake_lock("status") is not None
    except Exception:
        return None


__all__ = [
    "POWER_SUPPLY", "SHARED_STORAGE", "THERMAL", "battery", "interfaces",
    "soc_model", "storage", "thermal", "wake_lock_state",
]
