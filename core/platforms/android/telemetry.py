"""core/platforms/android/telemetry.py - host metrics on a phone, from /proc and /sys.

Android *is* Linux, so the ``/proc`` parsers are shared with the Linux backend
(``core.platforms.linux.procfs``); what a phone adds is what a server does not
have: a battery, thermal zones, and a shared-storage volume that is neither
``/`` nor ``$HOME``.

Nothing here needs ``termux-api``: battery percentage, charging state, voltage,
current and temperature all live in ``/sys/class/power_supply``.  The Termux:API
app is only required for things the Android sandbox genuinely forbids to a plain
process - camera, microphone, SMS, location - which is the device branch, not
telemetry.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.platforms import common
from core.platforms.android import sysfs
from core.platforms.linux import telemetry as linux_telemetry
from core.platforms.linux.procfs import ProcFS


def memory() -> Dict[str, float]:
    return linux_telemetry.memory()


def disks() -> List[tuple]:
    """Real volumes: the Termux home, the shared storage, then ``/data``."""
    rows = []
    for entry in sysfs.storage():
        rows.append((entry["path"], entry["total_gb"], entry["free_gb"]))
    return rows


def info_blocks() -> Dict[str, str]:
    usage = memory()
    return {
        "disks": common.disks_block(disks()),
        "memory": common.memory_block(
            usage["total_mb"], usage["used_mb"], usage["avail_mb"], usage["cached_mb"]),
        "load": common.load_block(
            None, common.cores(), common.iso_local(boot_epoch()),
            ProcFS().uptime(), common.load_average()),
        "top_processes": common.processes_block(ProcFS().top_processes()),
    }


def boot_epoch() -> Optional[float]:
    uptime = ProcFS().uptime()
    if uptime is None:
        return None
    import time
    return time.time() - uptime


def battery() -> Optional[Dict[str, Any]]:
    """Battery block: sysfs first, then ``termux-battery-status``.

    On a hardened Android (SELinux) an app cannot list ``/sys/class/power_supply``
    at all, so the Termux:API answer is the only source - and it is a good one:
    percentage, status, temperature, voltage and current, straight from
    ``BatteryManager``.  Its contract is ``{"ok": true, "value": {...}}``; a
    failure carries ``reason`` and is reported instead of a fake zero.
    """
    found = sysfs.battery()
    if found:
        return found
    try:
        from core.platforms.android import termux
    except Exception:  # pragma: no cover - partial checkout
        return None
    result = termux.run_json("termux-battery-status")
    if not isinstance(result, dict) or not result.get("ok"):
        reason = result.get("reason") if isinstance(result, dict) else ""
        return {
            "available": False,
            "reason": reason or "no battery source on this device",
            "fix": termux_fix(reason),
        }
    value = result.get("value")
    if not isinstance(value, dict):
        return {"available": False, "reason": "termux-battery-status returned no JSON"}
    status = str(value.get("status") or "").lower()
    plugged = str(value.get("plugged") or "").upper()
    return {
        "available": True,
        "source": "termux-battery-status",
        "percent": value.get("percentage"),
        "status": status,
        "charging": status in ("charging", "full") or plugged not in ("", "UNPLUGGED", "0"),
        "plugged": plugged or None,
        "temperature_c": value.get("temperature"),
        # The API answers microvolts/... in device units; keep them as reported.
        "voltage_mv": value.get("voltage"),
        "current_ma": value.get("current"),
        "health": value.get("health"),
        "cycle_count": value.get("cycle"),
    }


def termux_fix(reason: Any) -> str:
    """The one instruction that fixes a silent Termux:API failure, if that is it."""
    try:
        from core.platforms.android import termux
    except Exception:  # pragma: no cover
        return ""
    text = str(reason or "").lower()
    if "app" in text or "timeout" in text or "missing" in text:
        return termux.TERMUX_API_FIX
    return ""


def vitals() -> Dict[str, Any]:
    """``system_vitals`` payload for a phone: Linux numbers plus battery and heat."""
    usage = memory()
    load = common.load_average() or (0.0, 0.0, 0.0)
    home = os.path.expanduser("~")
    disk = common.disk_usage(home if os.path.isdir(home) else "/") or (0.0, 0.0, 0.0)
    total_gb, used_gb, free_gb = disk
    payload: Dict[str, Any] = {
        "hostname": common.hostname(),
        "os": "Android",
        "cpu_load": {"1m": float(load[0]), "5m": float(load[1]), "15m": float(load[2])},
        "ram": {
            "total_mb": usage["total_mb"],
            "used_mb": usage["used_mb"],
            "free_mb": usage["avail_mb"],
            "used_pct": common.pct(usage["used_mb"], usage["total_mb"]),
        },
        "disk": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": common.pct(used_gb, total_gb),
        },
    }
    battery_block = battery()
    if battery_block:
        payload["battery"] = battery_block
    thermal = sysfs.thermal(limit=4)
    if thermal:
        payload["thermal"] = thermal
    return payload


__all__ = ["battery", "boot_epoch", "disks", "info_blocks", "memory", "vitals"]
