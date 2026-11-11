#!/usr/bin/env python3
"""Live check of the Android platform backend, run ON the phone in Termux.

It exercises the same code paths the node uses for `system_info`, `system_vitals`,
`bash_exec` shell choice, job liveness and clipboard, and fails loudly instead of
printing a pretty report over a broken value.
"""

import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print("%-34s %s %s" % (name, "PASS" if ok else "FAIL", detail))


def main():
    from core import platforms

    scenario = platforms.detect()
    if scenario != "termux":
        print("this check is for an Android/Termux host; detected %r" % scenario)
        return 2
    check("detect() is termux", scenario == "termux", scenario)

    backend = platforms.backend()
    check("backend is AndroidBackend", backend.scenario == "termux", backend.label)

    name, argv = backend.shell()
    check("shell is bash/sh", name in ("bash", "sh"), "%s %s" % (name, argv))

    uptime = backend.uptime_seconds()
    try:
        open("/proc/uptime").read()
        visible = True
    except Exception:
        visible = False
    check("uptime matches /proc visibility", (uptime is not None) == visible,
          "%.0fs (visible=%s)" % (uptime or 0, visible))

    vitals = backend.vitals()
    ram = vitals.get("ram", {})
    disk = vitals.get("disk", {})
    check("vitals: os is Android", vitals.get("os") == "Android", str(vitals.get("os")))
    check("vitals: RAM total > 0", ram.get("total_mb", 0) > 0, "%.0f MB" % ram.get("total_mb", 0))
    check("vitals: disk total > 0", disk.get("total_gb", 0) > 0, "%.1f GB" % disk.get("total_gb", 0))
    battery = vitals.get("battery") or {}
    percent = battery.get("percent", -1)
    check("vitals: battery 0..100", 0 <= percent <= 100, "%s%% via %s" % (percent, battery.get("source")))
    check("vitals: charging flag is bool", isinstance(battery.get("charging"), bool), str(battery.get("charging")))
    thermal = vitals.get("thermal") or []
    check("vitals: thermal zones", bool(thermal), "%d zones, hottest %.1fC" % (
        len(thermal), thermal[0]["celsius"] if thermal else 0))

    blocks = backend.info_blocks()
    check("info blocks keys", set(blocks) == {"disks", "memory", "load", "top_processes"}, ",".join(sorted(blocks)))
    for key, text in blocks.items():
        check("block %s non-empty" % key, bool(text.strip()), text.splitlines()[0][:40])
    check("memory block has Mem:", "Mem:" in blocks.get("memory", ""))
    check("disks block has a mount", "Mount" in blocks.get("disks", ""))

    caps = backend.capabilities()
    check("caps: battery", caps.get("battery") is True, str(caps.get("battery")))
    check("caps: screenshot is honestly false", caps.get("screenshot") is False, "")

    pid = os.getpid()
    check("pid_alive(self)", backend.pid_alive(pid), "pid=%d" % pid)
    check("pid_start_time(self)", backend.pid_start_time(pid) is not None, "")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.8)
    check("pid_alive(child)", backend.pid_alive(child.pid), "pid=%d" % child.pid)
    backend.kill_tree(child.pid)
    time.sleep(0.8)
    check("kill_tree(child) dead", not backend.pid_alive(child.pid), "")
    try:
        child.kill()
    except Exception:
        pass

    text = "platform-check-%d" % int(time.time())
    ok = backend.clipboard_set(text)
    got = backend.clipboard_get() if ok else None
    # Android 10+ hides the clipboard from a background app: an empty read after a
    # successful write is the documented platform restriction, not a failure.
    clipboard_ok = ok and (got == text or got == "")
    check("clipboard write/read contract", clipboard_ok,
          "write=%s read=%r%s" % (ok, got, " (hidden from background app)" if ok and got == "" else ""))

    failed = [row for row in RESULTS if not row["ok"]]
    print("\nSUMMARY %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED: " + ", ".join(row["check"] for row in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


