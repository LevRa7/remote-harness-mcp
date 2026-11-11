"""Isolated container profile for ``rhm``.

The container is created inside the selected project (``.rhm-sandbox/``), mounts
only that directory at ``/workspace``, and runs with no host paths, no Docker
socket, dropped capabilities and a PID/memory limit.  The agent's shell and
background jobs execute inside it; the preinstalled toolchain can install more
packages *inside* the container, never on the host.

Requires Docker or Podman. If neither is available the menu reports it and the
normal (non-isolated) mode stays unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence

from core import control

IMAGE = "rhm-sandbox:24.04"
DOCKERFILE = """\
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates curl git jq ripgrep python3 python3-pip python3-venv \\
    nodejs npm build-essential pkg-config unzip zip nano vim-tiny \\
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["sleep", "infinity"]
"""

#: Directories that must never be mounted as /workspace: mounting them would
#: hand the container the host's system or the whole home directory.
_DANGEROUS = {
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot",
    "/dev", "/proc", "/sys", "/var", "/run", "/root",
}


def workspace_refusal(workspace: str) -> str:
    """Why *workspace* is too broad for the isolation profile ("" when safe)."""
    ws = os.path.realpath(os.path.expanduser(str(workspace or "").strip()))
    if not ws or not os.path.isdir(ws):
        return "Choose an existing project directory."
    if ws in _DANGEROUS:
        return "Refusing to mount a system directory (%s) as the project." % ws
    if os.name == "nt":
        drive, tail = os.path.splitdrive(ws)
        if tail in ("\\", "/"):
            return "Refusing to mount a whole drive (%s) as the project." % ws
        lowered = ws.lower()
        if lowered == os.path.expanduser("~").lower():
            return "Choose a project subdirectory, not the whole home directory."
        if lowered.startswith("c:\\windows") or lowered.startswith("c:\\program files"):
            return "Refusing to mount a Windows system directory."
    else:
        home = os.path.realpath(os.path.expanduser("~"))
        if ws == home:
            return "Choose a project subdirectory, not the whole home directory."
    return ""


def runtime() -> str:
    for name in ("docker", "podman"):
        if shutil.which(name):
            return name
    return ""


def secure_runtime(rt: str) -> str:
    """Use gVisor/Kata when the daemon advertises it, otherwise ""."""
    if not rt:
        return ""
    try:
        result = _run([rt, "info", "--format", "{{json .Runtimes}}"], timeout=10)
        if result.returncode != 0:
            return ""
        advertised = result.stdout.decode("utf-8", "replace").lower()
        for candidate in ("runsc", "kata-runtime", "kata"):
            if candidate in advertised:
                return candidate
    except Exception:
        pass
    return ""


def available() -> bool:
    return bool(runtime())


def container_name(workspace: str) -> str:
    digest = hashlib.sha256(os.path.realpath(workspace).encode("utf-8", "surrogateescape")).hexdigest()
    return "rhm-sandbox-" + digest[:12]


def project_dir(workspace: str) -> str:
    return os.path.join(os.path.realpath(workspace), ".rhm-sandbox")


def _run(argv: Sequence[str], timeout: int = 600, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _image_exists(rt: str) -> bool:
    try:
        result = _run([rt, "image", "inspect", IMAGE], timeout=20)
        return result.returncode == 0
    except Exception:
        return False


def _container_exists(rt: str, name: str) -> bool:
    try:
        result = _run([rt, "container", "inspect", name], timeout=20)
        return result.returncode == 0
    except Exception:
        return False


def enable(workspace: str, network: bool = False) -> Dict[str, Any]:
    """Build the image if needed, start the container and persist the state.

    ``network=False`` is the default: a suspicious project cannot reach the host,
    the LAN or the metadata service.  ``network=True`` is an explicit operator
    choice (needed only to install packages inside the container).
    """
    rt = runtime()
    if not rt:
        raise RuntimeError("Docker or Podman is not installed; isolated mode is unavailable.")
    refusal = workspace_refusal(workspace)
    if refusal:
        raise ValueError(refusal)
    ws = os.path.realpath(os.path.expanduser(str(workspace or "").strip()))

    sandbox_dir = project_dir(ws)
    os.makedirs(sandbox_dir, mode=0o700, exist_ok=True)
    if not os.path.realpath(sandbox_dir).startswith(ws + os.sep):
        raise ValueError("The .rhm-sandbox directory escapes the selected project.")
    dockerfile = os.path.join(sandbox_dir, "Dockerfile")
    with open(dockerfile, "w", encoding="utf-8") as handle:
        handle.write(DOCKERFILE)

    if not _image_exists(rt):
        build = _run([rt, "build", "-t", IMAGE, "-f", dockerfile, sandbox_dir], timeout=1800)
        if build.returncode != 0:
            raise RuntimeError(
                "Container image build failed: %s" % (build.stderr or build.stdout).decode("utf-8", "replace")[-1200:]
            )

    name = container_name(ws)
    if _container_exists(rt, name):
        _run([rt, "rm", "-f", name], timeout=60)
    isolated_runtime = secure_runtime(rt)
    command = [
        rt, "run", "-d", "--name", name, "--hostname", "rhm-sandbox",
        "--workdir", "/workspace",
        "--mount", "type=bind,source=%s,target=/workspace,bind-propagation=rprivate" % ws,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "--memory", "2g",
        "--cpus", "2",
        "--ulimit", "nofile=1024:2048",
        "--ulimit", "nproc=512:512",
        "--ipc", "private",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
        "--network", "bridge" if network else "none",
        IMAGE,
        "sleep", "infinity",
    ]
    if isolated_runtime:
        command[command.index("--network"):command.index("--network")] = ["--runtime", isolated_runtime]
    created = _run(command, timeout=120)
    if created.returncode != 0:
        raise RuntimeError(
            "Container start failed: %s" % (created.stderr or created.stdout).decode("utf-8", "replace")[-1200:]
        )

    state = {
        "enabled": True,
        "workspace": ws,
        "runtime": rt,
        "container": name,
        "image": IMAGE,
        "network": bool(network),
        "secure_runtime": isolated_runtime,
        "created_at": int(time.time()),
    }
    control.set_sandbox_state(state)
    return state


def disable(state: Optional[Dict[str, Any]] = None) -> None:
    current = state or control.sandbox_state() or {}
    rt = str(current.get("runtime") or runtime())
    name = str(current.get("container") or "")
    if rt and name:
        try:
            _run([rt, "rm", "-f", name], timeout=60)
        except Exception:
            pass
    control.set_sandbox_state(None)


def status() -> Dict[str, Any]:
    state = control.sandbox_state()
    return {
        "available": available(),
        "enabled": state is not None,
        "workspace": (state or {}).get("workspace"),
        "container": (state or {}).get("container"),
        "runtime": (state or {}).get("runtime") or runtime(),
        "network": bool((state or {}).get("network")),
        "secure_runtime": (state or {}).get("secure_runtime") or "",
    }


def exec_argv(command: str, state: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
    current = state or control.sandbox_state()
    if not current:
        return None
    rt = str(current.get("runtime") or "")
    name = str(current.get("container") or "")
    if not rt or not name:
        return None
    return [rt, "exec", "-i", "-w", "/workspace", name, "/bin/sh", "-lc", command]
