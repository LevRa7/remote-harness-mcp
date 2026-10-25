"""core/rekey.py - rotate a node's device token without reinstalling it.

Why this exists
---------------
The device token is the machine's identity: it opens the tunnel. Until now the
only way to replace a leaked one was to reinstall the node, which throws away the
node name, the link key and the job history. This module replaces that advice
with one command that works on every platform the node supports:

    python -m core.rekey            # systemd / launchd / Scheduled Task / runit
    remote-harness-mcp rotate       # the same, through the npm CLI

What it does, in order
----------------------
1. Checks the node is online (a rotation you cannot verify is a rotation that can
   lock you out).
2. Asks the gateway for a new token, authenticating with the current one.
3. Writes it everywhere the node keeps it: ``agent.env`` on every platform, the
   launchd plist on macOS and the Startup launcher on Windows (both of those bake
   the token into their own files, so editing only the env file would come back to
   life at the next logon).
4. Restarts the service the way that platform starts it.
5. Waits for the gateway to report the node online.
6. On failure, puts the old token back everywhere and restarts again: the gateway
   keeps the previous token valid until the node proves it can use the new one, so
   this rollback is always safe.

The gateway half is ``POST /v1/devices/<node>/rotate-token``; the previous token is
retired when the node connects with the new one, not when the rotation is
requested.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from core import domain, paths

CONFIG_NAME = "agent.env"
DEFAULT_TIMEOUT = 60
PLIST_NAMES = ("com.remoteharness.mcp.agent.plist", "com.antigravity.mesh.agent.plist")
SYSTEMD_UNITS = ("rhm-agent.service", "agy-agent.service")
STARTUP_LAUNCHERS = ("rhm-agent.vbs", "agy-agent.vbs")
WINDOWS_WATCHDOG_TASK = "RemoteHarnessMCPWatchdog"


# ---------------------------------------------------------------------------
# Reading and rewriting the configuration files
# ---------------------------------------------------------------------------

def config_path(explicit: Optional[str] = None) -> str:
    return explicit or os.environ.get("MESH_CONFIG_FILE") or paths.config_file(CONFIG_NAME)


def read_config(path: str) -> dict:
    return domain.read_env_file(path)


def write_env_value(path: str, key: str, value: str) -> bool:
    """Replace one ``KEY=VALUE`` line, leaving every other byte alone.

    The env file also carries ``MESH_GATEWAY``, ``MESH_USER`` and ``MESH_LINK_KEY``;
    rewriting the whole file from a parsed dict would quietly drop comments,
    blank lines and any key this version does not know about.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        lines = handle.read().splitlines()
    pattern = re.compile(r"^\s*%s\s*=" % re.escape(key))
    replaced, found = [], False
    for line in lines:
        if pattern.match(line):
            replaced.append("%s=%s" % (key, value))
            found = True
        else:
            replaced.append(line)
    if not found:
        replaced.append("%s=%s" % (key, value))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(replaced) + "\n")
    return found


def rewrite_plist(path: str, token: str) -> bool:
    """Set ``MESH_TOKEN`` inside a launchd plist.

    The plist spells the token as ``<key>MESH_TOKEN</key><string>...</string>``;
    only that one string is replaced, so the rest of the file (comments, other
    variables, KeepAlive) stays byte-identical.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    pattern = re.compile(
        r"(<key>\s*MESH_TOKEN\s*</key>\s*<string>)(.*?)(</string>)", re.DOTALL
    )
    if not pattern.search(text):
        return False
    replaced = pattern.sub(lambda m: m.group(1) + token + m.group(3), text, count=1)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(replaced)
    return True


def _read_text_preserving_encoding(path: str) -> tuple[str, str]:
    """Read a text file and remember how it was encoded.

    The Startup launcher is written by the installer as UTF-16 (PowerShell's
    ``-Encoding Unicode``), but a node installed by an older build may hold an
    ANSI copy, and a rotation must not die on the difference: it decodes what is
    there and writes the same shape back.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace"), "cp1252"


def rewrite_startup_launcher(path: str, token: str) -> bool:
    """Set the token in the Windows Startup ``.vbs`` launcher.

    The launcher exports ``MESH_TOKEN`` into the agent's environment before it
    starts it, and an environment variable outranks ``agent.env`` when the agent
    is launched that way. A rotation that skipped this file would therefore undo
    itself at the next logon.
    """
    text, encoding = _read_text_preserving_encoding(path)
    pattern = re.compile(r'(\(("|\')MESH_TOKEN\2\)\s*=\s*")([^"]*)(")')
    if not pattern.search(text):
        return False
    replaced = pattern.sub(lambda m: m.group(1) + token + m.group(4), text, count=1)
    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(replaced)
    return True


def plist_paths() -> list[str]:
    home = os.path.expanduser("~")
    agents = os.path.join(home, "Library", "LaunchAgents")
    return [os.path.join(agents, name) for name in PLIST_NAMES if os.path.exists(os.path.join(agents, name))]


def startup_launcher_paths() -> list[str]:
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    startup = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    return [os.path.join(startup, name) for name in STARTUP_LAUNCHERS if os.path.exists(os.path.join(startup, name))]


def apply_token_to_files(path: str, new_token: str, dry_run: bool = False) -> list[str]:
    """Write *new_token* into every file this platform reads it from.

    Returns the list of files that were (or, in a dry run, would be) changed.
    """
    changed: list[str] = []
    targets: list[str] = []
    if os.path.exists(path):
        targets.append(path)
    if platform.system() == "Darwin":
        targets.extend(plist_paths())
    if os.name == "nt":
        targets.extend(startup_launcher_paths())

    for target in targets:
        if dry_run:
            changed.append(target)
            continue
        if target == path:
            write_env_value(target, "MESH_TOKEN", new_token)
            changed.append(target)
        elif target.endswith(".plist"):
            if rewrite_plist(target, new_token):
                changed.append(target)
        elif target.endswith(".vbs"):
            if rewrite_startup_launcher(target, new_token):
                changed.append(target)
    return changed


# ---------------------------------------------------------------------------
# Gateway calls
# ---------------------------------------------------------------------------

def _post_json(url: str, token: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "User-Agent": "remote-harness-mcp-rekey",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def node_online(gateway: str, user: str, timeout: int = 10) -> Optional[bool]:
    """Ask the gateway whether this node's tunnel is up; None when unreachable."""
    url = "https://%s/health?user=%s" % (gateway, urllib.parse.quote(user))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return bool(payload.get("node_online"))
    except Exception:
        return None


def wait_online(gateway: str, user: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if node_online(gateway, user) is True:
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Restarting whatever supervises the agent
# ---------------------------------------------------------------------------

def _run(command: list[str], timeout: int = 30) -> int:
    try:
        return subprocess.run(command, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode
    except Exception:
        return 1


def _pkill(pattern: str) -> None:
    _run(["pkill", "-f", pattern])


def restart_agent(dry_run: bool = False) -> str:
    """Restart the agent the way this host starts it; returns what was used."""
    if dry_run:
        return "dry-run"
    system = platform.system()

    if system == "Windows":
        # The watchdog task starts the agent when it is missing, and it is the one
        # component that already knows the payload directory, the interpreter and
        # the log file. Killing the agent and running the task is therefore more
        # faithful than re-implementing the launch here.
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*core.agent*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=45)
        if _run(["schtasks.exe", "/Run", "/TN", WINDOWS_WATCHDOG_TASK]) == 0:
            return "scheduled task %s" % WINDOWS_WATCHDOG_TASK
        subprocess.Popen([sys.executable, "-u", "-m", "core.agent"],
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return "direct start"

    if system == "Darwin":
        uid = os.getuid()
        # launchd owns the process: kickstart restarts it in place and does not
        # depend on how the plist was loaded (bootstrap, legacy load, login item).
        for label in ("com.remoteharness.mcp.agent", "com.antigravity.mesh.agent"):
            if _run(["launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, label)]) == 0:
                return "launchd %s" % label
        for path in plist_paths():
            _run(["launchctl", "unload", path])
            if _run(["launchctl", "load", "-w", path]) == 0:
                return "launchd %s" % os.path.basename(path)
        _pkill("core.agent")
        return "direct start"

    # Linux and Termux.
    if domain.is_termux():
        if _run(["sv", "restart", "rhm-agent"]) == 0:
            return "runit rhm-agent"
        if _run(["sv", "restart", "agy-agent"]) == 0:
            return "runit agy-agent"
    for unit in SYSTEMD_UNITS:
        if _run(["systemctl", "--user", "restart", unit]) == 0:
            return "systemd %s" % unit
    _pkill("core.agent")
    subprocess.Popen([sys.executable, "-u", "-m", "core.agent"],
                     cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return "direct start"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.rekey",
        description="Rotate this node's device token without reinstalling it.",
    )
    parser.add_argument("--gateway", help="gateway host (default: MESH_GATEWAY / domain file)")
    parser.add_argument("--user", help="node name (default: MESH_USER / machine name)")
    parser.add_argument("--token", help="current device token (default: MESH_TOKEN)")
    parser.add_argument("--config-file", help="agent.env to edit (default: the standard path)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for the node to come back (default: %d)" % DEFAULT_TIMEOUT)
    parser.add_argument("--no-restart", action="store_true", help="write the token, do not restart anything")
    parser.add_argument("--dry-run", action="store_true", help="show what would change and stop")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = config_path(args.config_file)
    values = read_config(path)
    gateway = (args.gateway or values.get("MESH_GATEWAY") or domain.gateway_host() or "").strip()
    gateway = gateway.replace("https://", "").replace("http://", "").strip("/")
    user = (args.user or values.get("MESH_USER") or "").strip().lower()
    token = (args.token or os.environ.get("MESH_TOKEN") or values.get("MESH_TOKEN") or "").strip()

    def fail(code: int, message: str) -> int:
        if args.json:
            print(json.dumps({"status": "error", "error": message}, ensure_ascii=False))
        else:
            print("error: %s" % message, file=sys.stderr)
        return code

    if not gateway:
        return fail(2, "no gateway configured (set MESH_GATEWAY or pass --gateway)")
    if not user:
        return fail(2, "no node name configured (set MESH_USER or pass --user)")
    if not token:
        return fail(2, "no device token in %s; a standalone node has nothing to rotate" % path)

    online = node_online(gateway, user)
    if online is None:
        return fail(2, "gateway https://%s is unreachable" % gateway)
    if online is False:
        return fail(2, "node '%s' is offline on the gateway; start it and retry" % user)

    if args.dry_run:
        targets = apply_token_to_files(path, "<new-token>", dry_run=True)
        if args.json:
            print(json.dumps({"status": "dry-run", "gateway": gateway, "user": user,
                              "files": targets}, ensure_ascii=False))
        else:
            print("would rotate the device token for '%s' on https://%s" % (user, gateway))
            for target in targets:
                print("  would update %s" % target)
        return 0

    try:
        response = _post_json("https://%s/v1/devices/%s/rotate-token" % (gateway, urllib.parse.quote(user)), token)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8", "replace")).get("message", "")
        except Exception:
            pass
        return fail(3, "gateway refused the rotation (HTTP %d) %s" % (error.code, detail).strip())
    except Exception as error:
        return fail(3, "could not reach the gateway: %s" % error)

    new_token = str(response.get("token") or "")
    if not new_token:
        return fail(3, "gateway answered without a token: %r" % response)

    changed = apply_token_to_files(path, new_token)
    restart = "skipped" if args.no_restart else restart_agent()
    recovered = args.no_restart or wait_online(gateway, user, args.timeout)

    if not recovered:
        # The gateway still accepts the old token (it is retired only when the node
        # connects with the new one), so restoring it cannot make things worse.
        apply_token_to_files(path, token)
        restart_agent()
        if args.json:
            print(json.dumps({"status": "rolled-back", "user": user, "token": new_token,
                              "restart": restart}, ensure_ascii=False))
        else:
            print("the node did not come back within %ds; the old token was restored" % args.timeout)
            print("the new token (pending, still valid) is: %s" % new_token)
            print("if you saved it, put it in %s and restart the agent by hand" % path)
        return 4

    if args.json:
        print(json.dumps({"status": "rotated", "user": user, "gateway": gateway,
                          "restart": restart, "files": changed}, ensure_ascii=False))
    else:
        print("device token rotated for '%s' on https://%s" % (user, gateway))
        for target in changed:
            print("  updated %s" % target)
        print("  restarted via %s" % restart)
        print("the previous token is now invalid; the link key in MCP URLs is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
