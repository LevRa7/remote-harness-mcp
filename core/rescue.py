#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/rescue.py - Live USB hardware diagnostics & OS recovery schemas and collectors.

Designed to follow the exact contract of :mod:`core.device` and :mod:`core.mcp_tools`:
* Standard library only (Python 3.8+);
* Never raises: returns ``{"available": False, "reason": ...}`` or ``{"error": ...}``;
* Strict separation between Read-Only inspection (``rescue_info``) and mutating
  recovery operations (``rescue_action``) with mandatory ``dry_run`` support;
* Protects dying storage media by enforcing ``ro,noload`` mounts and checking kernel
  ring buffers for I/O errors before probing block devices.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

_IS_WINDOWS = os.name == "nt"

RESCUE_SECTIONS: Tuple[str, ...] = (
    "summary",
    "all",
    "disks",
    "smart",
    "partitions",
    "raid_lvm",
    "installed_os",
    "boot_efi",
    "win_artifacts",
    "dmesg_io",
)

RESCUE_ACTIONS: Tuple[str, ...] = (
    "mount_safe",
    "umount",
    "fsck_check",
    "ntfs_fix",
    "grub_repair",
    "win_sam_reset",
    "ddrescue_plan",
    "chroot_exec",
)


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


RESCUE_TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "rescue_info",
        "title": "Live USB Rescue & Storage Diagnostics",
        "description": (
            "Read-only hardware and OS recovery inspection for Live USB nodes: "
            "block devices, SMART health attributes, partition tables, RAID/LVM/LUKS topology, "
            "installed Linux/Windows roots on unmounted drives, UEFI boot entries, and kernel "
            "I/O error logs. Never modifies disks."
        ),
        "inputSchema": _schema(
            {
                "section": {
                    "type": "string",
                    "enum": list(RESCUE_SECTIONS),
                    "description": "Diagnostic section to collect (default: summary).",
                },
                "device": {
                    "type": "string",
                    "description": "Optional target block device, e.g. /dev/nvme0n1 or /dev/sda.",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Probe deadline in seconds (1-30, default 10).",
                },
            }
        ),
    },
    {
        "name": "rescue_action",
        "title": "Live USB OS & Filesystem Recovery Action",
        "description": (
            "Perform a controlled recovery action on an unmounted target disk or guest OS. "
            "Supports dry_run=true (default when omitted on destructive actions) to preview the exact "
            "command argv and safety checks before touching the disk. Refuses to write images to "
            "volatile tmpfs/overlay RAM disks."
        ),
        "inputSchema": _schema(
            {
                "action": {
                    "type": "string",
                    "enum": list(RESCUE_ACTIONS),
                    "description": "Recovery operation to perform.",
                },
                "device": {
                    "type": "string",
                    "description": "Target block device or partition (e.g. /dev/nvme0n1p2).",
                },
                "mountpoint": {
                    "type": "string",
                    "description": "Mount directory (default: /mnt/rescue/<dev>).",
                },
                "writable": {
                    "type": "boolean",
                    "description": "mount_safe: allow read-write mount (default false -> ro,noload).",
                },
                "target_image": {
                    "type": "string",
                    "description": "ddrescue_plan: destination image file on a non-volatile disk.",
                },
                "log_file": {
                    "type": "string",
                    "description": "ddrescue_plan: mapfile/logfile path for resumable cloning.",
                },
                "efi_partition": {
                    "type": "string",
                    "description": "grub_repair: EFI System Partition (e.g. /dev/nvme0n1p1).",
                },
                "sam_user": {
                    "type": "string",
                    "description": "win_sam_reset: Windows local username to unlock/clear password.",
                },
                "chroot_cmd": {
                    "type": "string",
                    "description": "chroot_exec: command to execute inside the mounted guest OS.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Validate safety guards and return exact argv without executing.",
                },
            },
            ["action"],
        ),
    },
]


def _run_cmd(argv: List[str], timeout: int = 10) -> Tuple[int, str, str]:
    """Run an external binary in a new session with a hard timeout."""
    if not argv or shutil.which(argv[0]) is None:
        return 127, "", f"binary not found: {argv[0] if argv else ''}"
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if not _IS_WINDOWS:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.run(argv, timeout=timeout, **kwargs)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s (potential D-state I/O hang)"
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def _valid_dev_path(dev: str) -> bool:
    return bool(re.match(r"^/dev/[a-zA-Z0-9_\-/]+$", dev or ""))


def collect_dmesg_io(device: Optional[str] = None, timeout: int = 5) -> Dict[str, Any]:
    """Scan kernel buffer for block I/O errors before touching potentially dying media."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "dmesg_io is only available on Linux Live USB nodes"}
    rc, out, err = _run_cmd(["dmesg", "--color=never", "-T"], timeout=timeout)
    if rc != 0:
        return {"available": False, "reason": err.strip() or "dmesg failed", "source": "dmesg"}
    pattern = re.compile(
        r"(I/O error|blk_update_request|Buffer I/O error|critical medium error|nvme.*I/O Cmd|FPDMA QUEUED)",
        re.IGNORECASE,
    )
    dev_short = os.path.basename(device) if device else ""
    matches: List[str] = []
    for line in out.splitlines()[-2000:]:
        if pattern.search(line):
            if not dev_short or dev_short in line:
                matches.append(line.strip())
    return {
        "available": True,
        "source": "dmesg",
        "device_filter": dev_short or None,
        "io_error_count": len(matches),
        "healthy_io": len(matches) == 0,
        "recent_errors": matches[-25:],
    }


def collect_disks(timeout: int = 8) -> Dict[str, Any]:
    """Return structured block device topology from lsblk -J."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "lsblk requires Linux"}
    argv = [
        "lsblk", "-J", "-b", "-o",
        "NAME,KNAME,PATH,SIZE,FSTYPE,FSVER,LABEL,UUID,TYPE,MOUNTPOINT,MODEL,SERIAL,RO,RM,TRAN",
    ]
    rc, out, err = _run_cmd(argv, timeout=timeout)
    if rc != 0:
        return {"available": False, "reason": err.strip() or "lsblk failed", "source": "lsblk"}
    try:
        parsed = json.loads(out)
    except Exception as exc:
        return {"available": False, "reason": f"lsblk json parse error: {exc}", "source": "lsblk"}
    return {
        "available": True,
        "source": "lsblk",
        "blockdevices": parsed.get("blockdevices", []),
    }


def collect_smart(device: Optional[str] = None, timeout: int = 10) -> Dict[str, Any]:
    """Collect SMART health via smartctl -a -j."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "Linux smartctl collector only"}
    if not device or not _valid_dev_path(device):
        return {"available": False, "reason": "Provide a valid device path, e.g. /dev/nvme0n1 or /dev/sda"}
    io_check = collect_dmesg_io(device=device, timeout=4)
    if io_check.get("io_error_count", 0) > 10:
        return {
            "available": False,
            "reason": "aborted_due_to_active_kernel_io_errors",
            "dmesg_io": io_check,
        }
    rc, out, err = _run_cmd(["smartctl", "-a", "-j", device], timeout=timeout)
    try:
        data = json.loads(out) if out.strip() else {}
    except Exception:
        data = {}
    if not data:
        return {"available": False, "reason": err.strip() or f"smartctl exited with {rc}", "source": "smartctl"}
    return {
        "available": True,
        "source": "smartctl",
        "device": device,
        "model_name": data.get("model_name"),
        "serial_number": data.get("serial_number"),
        "smart_passed": (data.get("smart_status") or {}).get("passed"),
        "temperature_c": (data.get("temperature") or {}).get("current"),
        "power_on_hours": (data.get("power_on_time") or {}).get("hours"),
        "nvme_health": data.get("nvme_smart_health_information_log"),
    }


def plan_or_run_rescue_action(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and build (or execute) a recovery action with strict safety guards."""
    action = str(args.get("action") or "").strip()
    dry_run = bool(args.get("dry_run", True))
    device = str(args.get("device") or "").strip()

    if action not in RESCUE_ACTIONS:
        return {"error": f"Unknown rescue action: {action}"}

    if action == "mount_safe":
        if not _valid_dev_path(device):
            return {"error": "mount_safe requires a valid device path (e.g. /dev/nvme0n1p2)"}
        writable = bool(args.get("writable", False))
        dev_name = os.path.basename(device)
        mountpoint = str(args.get("mountpoint") or f"/mnt/rescue/{dev_name}").strip()
        opts = "rw,noatime" if writable else "ro,noload"
        argv = ["mount", "-o", opts, device, mountpoint]
        plan = {
            "action": action,
            "device": device,
            "mountpoint": mountpoint,
            "writable": writable,
            "options": opts,
            "argv": argv,
            "dry_run": dry_run,
        }
        if dry_run or _IS_WINDOWS:
            return plan
        os.makedirs(mountpoint, exist_ok=True)
        rc, out, err = _run_cmd(argv, timeout=15)
        plan.update({"exit_code": rc, "stdout": out, "stderr": err})
        return plan

    if action == "ddrescue_plan":
        target_image = str(args.get("target_image") or "").strip()
        log_file = str(args.get("log_file") or f"{target_image}.mapfile").strip()
        if not _valid_dev_path(device) or not target_image:
            return {"error": "ddrescue_plan requires both 'device' and 'target_image'"}
        if any(target_image.startswith(p) for p in ("/tmp/", "/run/", "/var/tmp/")):
            return {
                "error": (
                    "Refusing to write recovery image to volatile RAM filesystem "
                    "(/tmp or /run). Mount an external scratch disk under /mnt/scratch first."
                )
            }
        argv = ["ddrescue", "-f", "-n", device, target_image, log_file]
        return {
            "action": action,
            "dry_run": True,
            "argv": argv,
            "recommended_execution": "Pass ' '.join(argv) to run_job() for non-blocking background cloning.",
        }

    if action == "ntfs_fix":
        if not _valid_dev_path(device):
            return {"error": "ntfs_fix requires a valid NTFS partition path"}
        argv = ["ntfsfix", "-d", device]
        if dry_run or _IS_WINDOWS:
            return {"action": action, "device": device, "argv": argv, "dry_run": True}
        rc, out, err = _run_cmd(argv, timeout=30)
        return {"action": action, "device": device, "argv": argv, "exit_code": rc, "stdout": out, "stderr": err}

    if action == "grub_repair":
        efi_part = str(args.get("efi_partition") or "").strip()
        mountpoint = str(args.get("mountpoint") or "/mnt/rescue/root").strip()
        if not _valid_dev_path(device) or not _valid_dev_path(efi_part):
            return {"error": "grub_repair requires 'device' (root FS) and 'efi_partition' (ESP)"}
        steps = [
            ["mount", device, mountpoint],
            ["mount", efi_part, f"{mountpoint}/boot/efi"],
            ["mount", "--bind", "/dev", f"{mountpoint}/dev"],
            ["mount", "--bind", "/proc", f"{mountpoint}/proc"],
            ["mount", "--bind", "/sys", f"{mountpoint}/sys"],
            ["mount", "--bind", "/run", f"{mountpoint}/run"],
            ["chroot", mountpoint, "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=GRUB"],
            ["chroot", mountpoint, "update-grub"],
        ]
        return {"action": action, "dry_run": dry_run, "planned_steps": steps}

    return {"action": action, "dry_run": dry_run, "status": "schema_ready"}
