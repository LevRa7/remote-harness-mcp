#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/server.py - Remote Harness MCP Standalone MCP Server (superset module).

This module serves two purposes:

1. Typed coordinator vitals helper used by the cloud/tunnel deployment:
   ``get_coordinator_vitals() -> NodeVitals`` (imported by ``rhm_mcp_server.py``
   and by the typed integration / resilience tests).

2. A fully self-contained, stdlib-only MCP (Model Context Protocol) HTTP server
   for the local "standalone" mode, launched by ``install.sh`` as::

       python3 -m core.server --host 127.0.0.1 --port=8096

   Endpoints:
       GET  /                     -> health JSON
       GET  /health               -> health JSON
       GET  /sse, /mcp            -> text/event-stream (MCP SSE transport)
       POST /messages, /sse, /mcp -> JSON-RPC 2.0
       OPTIONS                    -> CORS preflight
       other paths                -> 404

SECURITY
--------
``--host`` defaults to loopback (``127.0.0.1``) and must NEVER be changed to
``0.0.0.0`` for a default install: this server can execute arbitrary shell
commands (``bash_exec``) and read arbitrary files (``read_file``). A token is
always required; when ``--token``/``MESH_TOKEN`` is absent the server generates
one and writes it 0600 to the config directory. Binding a public interface still
requires a firewall - a generated token is a credential, not a substitute for
network isolation.

The module depends only on the Python standard library plus the pre-existing
local helpers ``core.schemas`` (pydantic) and ``core.vitals``. Both are treated
as optional so the server still starts under a bare interpreter (e.g.
``/usr/bin/python3`` without pydantic): ``get_coordinator_vitals()`` then
returns a light-weight stand-in object exposing the very same attributes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlsplit, urlunsplit

logger = logging.getLogger("remote-harness-mcp.standalone")

# ---------------------------------------------------------------------------
# Optional local dependencies.
#
# The MCP server must keep working when pydantic (and therefore
# ``core.schemas``) is not installed, which is the case for the bare system
# interpreter that systemd uses on a fresh standalone install. Importing them
# lazily/guarded keeps ``python3 -m core.server`` functional while preserving
# the exact ``NodeVitals`` return type whenever pydantic *is* available.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly by the environment
    from core.schemas import NodeVitals  # type: ignore
except Exception as _schemas_import_error:  # pragma: no cover
    NodeVitals = None  # type: ignore
    logger.debug("core.schemas unavailable (%s); using fallback vitals object", _schemas_import_error)

try:  # pragma: no cover - exercised implicitly by the environment
    from core.vitals import get_host_vitals as _core_get_host_vitals  # type: ignore
except Exception as _vitals_import_error:  # pragma: no cover
    _core_get_host_vitals = None  # type: ignore
    logger.debug("core.vitals unavailable (%s); using inline vitals collector", _vitals_import_error)

# Shared tool logic.  core.mcp_tools is stdlib-only, so this import always works.
from core import landing  # noqa: E402
from core import mcp_tools  # noqa: E402

# Public file shares (core/web_share.py) are optional: a checkout from before the
# share surface existed still runs every other tool. The standalone server is the
# public endpoint of a Funnel node, so it must serve those shares itself - the
# gateway relay that does it for a tunnel node is not in this path at all.
try:  # pragma: no cover - the module ships with this checkout
    from core import web_share as _web_share  # noqa: E402
except Exception as _web_share_import_error:  # pragma: no cover
    _web_share = None  # type: ignore
    logger.debug("core.web_share unavailable (%s); public shares are disabled", _web_share_import_error)


class _FallbackVitals(object):
    """Minimal stand-in for :class:`core.schemas.NodeVitals` without pydantic."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    def model_dump(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def model_dump_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, default=str, indent=indent)

    def dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def json(self, indent: Optional[int] = None) -> str:
        return self.model_dump_json(indent=indent)

    def __repr__(self) -> str:
        return "NodeVitals(%r)" % (self.__dict__,)


def _fallback_host_vitals() -> Dict[str, Any]:
    """Stdlib-only equivalent of ``core.vitals.get_host_vitals`` (last resort)."""
    system = platform.system()
    load = {"1m": 0.0, "5m": 0.0, "15m": 0.0}
    try:
        if hasattr(os, "getloadavg"):
            la = os.getloadavg()
            load = {"1m": round(la[0], 2), "5m": round(la[1], 2), "15m": round(la[2], 2)}
    except Exception:
        pass

    total_mb = 0.0
    free_mb = 0.0
    if system == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_mb = round(int(line.split()[1]) / 1024.0, 1)
                    elif line.startswith("MemAvailable:"):
                        free_mb = round(int(line.split()[1]) / 1024.0, 1)
        except Exception:
            pass

    used_mb = max(0.0, round(total_mb - free_mb, 1))
    used_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0
    try:
        du = shutil.disk_usage("C:\\" if system == "Windows" else "/")
        disk = {
            "total_gb": round(du.total / (1024 ** 3), 2),
            "used_gb": round(du.used / (1024 ** 3), 2),
            "free_gb": round(du.free / (1024 ** 3), 2),
            "used_pct": round((du.used / du.total) * 100.0, 1) if du.total > 0 else 0.0,
        }
    except Exception:
        disk = {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "used_pct": 0.0}

    return {
        "hostname": socket.gethostname(),
        "os": system,
        "cpu_load": load,
        "ram": {"total_mb": total_mb, "used_mb": used_mb, "free_mb": free_mb, "used_pct": used_pct},
        "disk": disk,
    }


def get_coordinator_vitals() -> "NodeVitals":
    """Collect this coordinator's typed vitals.

    Behaviour is unchanged from the tunnel deployment: it returns a
    ``core.schemas.NodeVitals`` instance whenever pydantic is importable. When
    pydantic is unavailable (bare system interpreter) an attribute-compatible
    fallback object is returned instead so the standalone server keeps running.
    """
    hostname = socket.gethostname()
    ip = "127.0.0.1"
    try:
        load = os.getloadavg()[0]
    except Exception:
        load = 0.0

    # ``/proc/meminfo`` and ``/proc/uptime`` are Linux-only. Ask the shared
    # cross-platform collector first (core.vitals knows the Windows and macOS
    # paths), and keep the inline /proc parsing as the last resort.
    native: Optional[Dict[str, Any]] = None
    if _core_get_host_vitals is not None:
        try:
            candidate = _core_get_host_vitals()
            if isinstance(candidate, dict):
                native = candidate
        except Exception:
            native = None

    total_mb = 1
    used_mb = 0
    if native:
        ram = native.get("ram") or {}
        try:
            total_mb = int(ram.get("total_mb") or 0) or 1
            used_mb = max(0, int(ram.get("used_mb") or 0))
        except (TypeError, ValueError):
            total_mb, used_mb = 1, 0
    else:
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem = {}
            for line in lines:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    if k in ["MemTotal", "MemAvailable"]:
                        mem[k] = int(v) // 1024
            if "MemTotal" in mem and "MemAvailable" in mem:
                total_mb = mem["MemTotal"]
                avail_mb = mem["MemAvailable"]
                used_mb = max(0, total_mb - avail_mb)
        except Exception:
            pass

    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
    usage_pct = min(100.0, max(0.0, usage_pct))

    total_disk_gb = 1.0
    free_disk_gb = 0.0
    if native:
        disk = native.get("disk") or {}
        try:
            total_disk_gb = float(disk.get("total_gb") or 0.0) or 1.0
            free_disk_gb = max(0.0, float(disk.get("free_gb") or 0.0))
        except (TypeError, ValueError):
            total_disk_gb, free_disk_gb = 1.0, 0.0
    else:
        try:
            d = shutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
            total_disk_gb = round(d.total / (1024 ** 3), 2)
            free_disk_gb = round(d.free / (1024 ** 3), 2)
        except Exception:
            pass

    uptime_str = "unknown"
    uptime_seconds = mcp_tools.host_uptime_seconds()
    if uptime_seconds is not None:
        hours = int(uptime_seconds // 3600)
        mins = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{hours}h {mins}m"

    fields = dict(
        hostname=hostname,
        ip=ip,
        is_online=True,
        cpu_load_1m=round(load, 2),
        ram_used_mb=used_mb,
        ram_total_mb=total_mb,
        ram_usage_pct=usage_pct,
        disk_free_gb=free_disk_gb,
        disk_total_gb=total_disk_gb,
        uptime=uptime_str,
        timestamp=datetime.now(timezone.utc),
    )
    if NodeVitals is not None:
        return NodeVitals(**fields)
    return _FallbackVitals(**fields)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Output helpers (mirror core/agent.py sanitize_output/truncate_output without
# importing it, because core.agent imports websockets at module import time).
# ---------------------------------------------------------------------------

# Chunk size for one response. Larger than before (12000) so ordinary command
# output arrives whole; anything bigger is paginated via next_cursor rather than
# silently truncated, and very large output is spooled to a file on the node.
MAX_OUTPUT_CHARS = 50000

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def sanitize_output(text: str) -> str:
    """Strip ANSI escape codes and non-printable control characters."""
    if not text:
        return ""
    text = ANSI_ESCAPE_RE.sub("", text)
    return "".join(
        ch for ch in text if ch in ("\n", "\r", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )


def truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Keep head+tail and insert an explicit ``... Output truncated ...`` notice."""
    if len(text) <= max_chars:
        return text
    head_size = int(max_chars * 0.7)   # 8400 chars
    tail_size = int(max_chars * 0.25)  # 3000 chars
    omitted = len(text) - (head_size + tail_size)
    omitted_lines = text[head_size:-tail_size].count("\n")
    notice = (
        f"\n\n... [Output truncated: {omitted} characters / ~{omitted_lines} lines hidden "
        f"to prevent context overflow. Use head, tail, grep, or write to file for full data] ...\n\n"
    )
    return text[:head_size] + notice + text[-tail_size:]


def _workspace_dir() -> str:
    """Base directory used to resolve relative paths (MESH_WORKSPACE or cwd)."""
    return os.environ.get("MESH_WORKSPACE") or os.getcwd()


# ---------------------------------------------------------------------------
# Tool logic now lives in core/mcp_tools.py; the helpers below only delegate.
# ---------------------------------------------------------------------------

def _load_orchestration_skill() -> str:
    """Read the live orchestrator skill through the shared tool module."""
    return mcp_tools.call_tool("get_orchestration_skill", {})


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

# Metadata advertised through ``tools/list`` - the single source of truth is
# core/mcp_tools.ALL_TOOLS (filtered dynamically via mcp_tools.list_tools()).
TOOL_DEFINITIONS: List[Dict[str, Any]] = mcp_tools.ALL_TOOLS

_REQUIRED_ARGS: Dict[str, Tuple[str, ...]] = {
    "bash_exec": ("command",),
    "read_file": ("path",),
    "read_media": ("path",),
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_string", "new_string"),
    "grep_search": ("pattern",),
    "glob_find": ("pattern",),
    "run_job": ("command",),
    "job_output": ("job_id",),
    "job_kill": ("job_id",),
    "share_file": ("path",),
    "serve_dir": ("path",),
    "unshare": ("name",),
    "fetch_url": ("url",),
}


def _make_tool_callable(tool_name: str) -> Callable[..., Any]:
    """Wrap a shared tool as an old-style callable (``f(**kwargs) -> dict``)."""

    def _call(**kwargs: Any) -> Any:
        return mcp_tools.call_tool(tool_name, kwargs)

    _call.__name__ = str(tool_name)
    return _call


class _InvalidParams(Exception):
    """Raised internally when a ``tools/call`` argument fails validation."""


class RemoteHarnessMCPServer(object):
    """Standalone MCP server: a registry of tools plus a JSON-RPC dispatcher."""

    name = "remote_harness_mcp"
    version = "1.0.0"

    #: Versions this server can speak, newest first. The one the client asks for
    #: wins when it is on the list: a strict validator that offers 2025-06-18 must
    #: not be answered with 2024-11-05, which some clients read as "not an MCP
    #: server".
    PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

    def __init__(self, name: str = "remote_harness_mcp") -> None:
        self.name = name
        #: Streamable HTTP session id, echoed to clients that care (see the spec).
        self.session_id = secrets.token_hex(16)
        self.tools: Dict[str, Callable[..., Any]] = {}
        self._specs: Dict[str, Dict[str, Any]] = {
            spec["name"]: spec for spec in TOOL_DEFINITIONS
        }
        self._spec_order: List[str] = [spec["name"] for spec in TOOL_DEFINITIONS]
        self._register_default_tools()

    # -- tool registry ----------------------------------------------------
    def _register_default_tools(self) -> None:
        for spec in TOOL_DEFINITIONS:
            self.register_tool(spec["name"], _make_tool_callable(spec["name"]))

    def register_tool(self, name: str, func: Callable[..., Any]) -> None:
        self.tools[name] = func
        if name not in self._specs:
            self._specs[name] = {
                "name": name,
                "title": name,
                "description": "Custom tool.",
                "inputSchema": {"type": "object", "properties": {}},
            }
        if name not in self._spec_order:
            self._spec_order.append(name)

    def list_tool_specs(self) -> List[Dict[str, Any]]:
        active = mcp_tools.list_tools()
        active_names = {spec["name"] for spec in active}
        specs = [spec for spec in active if spec["name"] in self.tools]
        builtin_names = {spec["name"] for spec in mcp_tools.ALL_TOOLS}
        for name in self._spec_order:
            if name in self.tools and name not in builtin_names and name not in active_names:
                specs.append(self._specs[name])
        return specs

    # -- tool implementations (delegate to the shared implementation) ------
    def system_vitals(self) -> Dict[str, Any]:
        return mcp_tools.call_tool("system_vitals", {})

    def bash_exec(self, command: str) -> Dict[str, Any]:
        return mcp_tools.call_tool("bash_exec", {"command": command})

    def get_orchestration_skill(self) -> str:
        return mcp_tools.call_tool("get_orchestration_skill", {})

    def list_dir(self, path: str = "") -> Dict[str, Any]:
        return mcp_tools.call_tool("list_dir", {"path": path})

    def read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> Dict[str, Any]:
        return mcp_tools.call_tool(
            "read_file", {"path": path, "start_line": start_line, "end_line": end_line}
        )

    # -- JSON-RPC dispatch ------------------------------------------------
    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def handle_jsonrpc(self, body: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Handle one JSON-RPC message.

        Returns ``(http_status, response_or_None)``. ``(204, None)`` means the
        message was a notification and no body must be sent.
        """
        try:
            return self._handle_jsonrpc(body)
        except Exception:  # never let an unexpected failure drop the connection
            logger.exception("Unhandled error while processing JSON-RPC request")
            return 200, self._error(None, -32603, "Internal error")

    def _handle_jsonrpc(self, body: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
        # -- parse stage ---------------------------------------------------
        if isinstance(body, (bytes, bytearray)):
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception:
                return 200, self._error(None, -32700, "Parse error")
        elif isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                return 200, self._error(None, -32700, "Parse error")

        # -- structural stage ---------------------------------------------
        if isinstance(body, list):
            # Batches are explicitly unsupported, but the response is a normal
            # JSON-RPC error and the connection stays alive.
            return 200, self._error(None, -32600, "Invalid Request: batch requests are not supported")
        if not isinstance(body, dict):
            return 200, self._error(None, -32600, "Invalid Request")

        # ONLY an absent "id" means notification; an explicit "id": null is a
        # real request that must receive a response.
        if "id" not in body:
            return 204, None

        req_id = body.get("id")
        method = body.get("method")
        if not isinstance(method, str) or not method:
            return 200, self._error(req_id, -32600, "Invalid Request: missing or invalid method")

        params = body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return 200, self._error(req_id, -32602, "Invalid params: 'params' must be an object")

        # -- method dispatch ----------------------------------------------
        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested in self.PROTOCOL_VERSIONS:
                negotiated = requested
            else:
                negotiated = self.PROTOCOL_VERSIONS[0]
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": negotiated,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": self.name, "version": self.version},
                    # Short on purpose: the tool descriptions carry the detail. The
                    # one thing worth saying up front is where the device branch
                    # lives, because a phone node (Termux) is exactly the case where
                    # a model otherwise hand-writes termux-* commands through
                    # bash_exec instead of using the typed tools.
                    "instructions": ("Standalone Local Remote Harness MCP Server. "
                                     "For the device this node runs on - battery, signal, "
                                     "time, sensors, cameras, microphones, notifications, "
                                     "torch, volume - use device_info / device_control; "
                                     "device_capture and device_messages need no confirmation, "
                                     "and the node's MESH_DEVICE switches decide what is allowed."),
                },
            }

        if method in ("notifications/initialized", "notifications/cancelled"):
            # Reached only when the client (incorrectly) sent an id: acknowledge.
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "ping":
            return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.list_tool_specs()},
            }

        if method == "tools/call":
            return self._handle_tools_call(req_id, params)

        if method == "prompts/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": [
                        {"name": "rhm-orchestrator", "description": "Local Standalone Orchestrator"}
                    ]
                },
            }

        if method == "prompts/get":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": "Remote Harness Orchestrator Persona",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": self.get_orchestration_skill()},
                        }
                    ],
                },
            }

        if method == "resources/list":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": "resource://skills/orchestrator.md",
                            "name": "orchestrator.md",
                            "mimeType": "text/markdown",
                        }
                    ]
                },
            }

        if method == "resources/read":
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "contents": [
                        {
                            "uri": params.get("uri", ""),
                            "mimeType": "text/markdown",
                            "text": self.get_orchestration_skill(),
                        }
                    ]
                },
            }

        return 200, self._error(req_id, -32601, "Method not found: %s" % method)

    # -- tools/call -------------------------------------------------------
    def _handle_tools_call(self, req_id: Any, params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return 200, self._error(req_id, -32602, "Invalid params: 'name' is required")
        if name not in self.tools:
            return 200, self._error(req_id, -32601, "Unknown tool: %s" % name)

        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return 200, self._error(req_id, -32602, "Invalid params: 'arguments' must be an object")
        # Underscore keys are gateway-owned (``_origin=web`` tells the node a
        # logged-in cabinet asked). A client of this standalone server is not the
        # gateway, so it may not claim them.
        arguments = {key: value for key, value in arguments.items()
                     if not str(key).startswith("_")}

        try:
            self._validate_arguments(name, arguments)
        except _InvalidParams as exc:
            return 200, self._error(req_id, -32602, "Invalid params: %s" % exc)

        if name in ("bash_exec", "run_script", "run_job") and str(
            os.environ.get("MESH_ENABLE_SHELL", "1")
        ).strip().lower() in ("0", "false", "no", "off"):
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": "shell_disabled",
                                    "message": "Shell execution is disabled (--no-shell / MESH_ENABLE_SHELL=0).",
                                }
                            ),
                        }
                    ],
                    "isError": True,
                },
            }

        try:
            value = mcp_tools.call_tool(name, arguments)
        except Exception as exc:  # defensive: call_tool itself never raises
            # A failing tool is an MCP tool error, NOT a JSON-RPC error.
            logger.exception("Tool %r raised", name)
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": "Tool execution failed: %s: %s" % (type(exc).__name__, exc)}
                    ],
                    "isError": True,
                },
            }

        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": self._result_to_content(value),
                "isError": self._is_error_result(value),
            },
        }

    @classmethod
    def _result_to_content(cls, value: Any) -> List[Dict[str, Any]]:
        """The MCP content array for one tool result.

        Almost every tool answers with data, and a single text block is the honest
        shape for that. A media tool is different: it returns ``image`` and
        ``audio`` blocks whose base64 must reach the client byte for byte, so a
        result that already is a content array is forwarded rather than described.
        The blocks are validated on the way out - a block of a type this server
        does not know is a protocol invention the client cannot render, so it is
        dropped instead of breaking the whole answer.
        """
        blocks = value.get("content") if isinstance(value, dict) else None
        if isinstance(blocks, list):
            forwarded = [block for block in blocks
                         if isinstance(block, dict)
                         and block.get("type") in mcp_tools.MEDIA_BLOCK_TYPES]
            if forwarded:
                return forwarded
        return [{"type": "text", "text": cls._result_to_text(value)}]

    def _validate_arguments(self, name: str, arguments: Dict[str, Any]) -> None:
        for key in _REQUIRED_ARGS.get(name, ()):  # required arguments
            if key not in arguments or arguments[key] is None:
                raise _InvalidParams("'%s' is required" % key)
        if name == "bash_exec":
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                raise _InvalidParams("'command' is required and must be a non-empty string")
        if name in ("list_dir", "read_file"):
            path = arguments.get("path")
            if path is not None and not isinstance(path, str):
                raise _InvalidParams("'path' must be a string")
        if name == "read_file":
            for key in ("start_line", "end_line"):
                value = arguments.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise _InvalidParams("'%s' must be an integer" % key)

    @staticmethod
    def _is_error_result(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("error"):
                return True
            if value.get("ok") is False:
                return True
            if "exit_code" in value:
                return value.get("exit_code") not in (0, None)
        return False

    @staticmethod
    def _result_to_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


#: Port the standalone server binds. Only used to name a share link when the node
#: has no public host of its own (a local-only install).
_STANDALONE_PORT = 8096


def _standalone_public_url() -> str:
    """The address this standalone node is really reached at.

    A Funnel install publishes the node on its own ``<node>.<tailnet>.ts.net``
    name, and the installer records that name in ``MESH_ALLOWED_HOSTS`` (and,
    from this version on, in ``MESH_PUBLIC_URL``). An explicit value wins; the
    allowlist is the next best source. The legacy gateway domain is consulted
    only after both, because a Funnel node never dials that gateway - a link
    built from it points at a bridge that has no tunnel to relay through, which
    is exactly how a published share answered 404.
    """
    explicit = (os.environ.get("MESH_PUBLIC_URL")
                or os.environ.get("AGY_PUBLIC_BASE_URL") or "").strip()
    if explicit:
        return explicit
    # allowed_public_hosts() only returns plain dotted names it validated, so a
    # malformed or loopback entry can never become a link.
    for host in sorted(allowed_public_hosts()):
        return "https://" + host
    # Deliberately NOT domain.public_base_url(): a standalone node is not behind
    # that gateway, and its legacy domain file (left by a tunnel-era install) is
    # how a share link ended up on a bridge with no tunnel into this machine.
    return "http://localhost:%d" % _STANDALONE_PORT


def configure_tools_from_env() -> None:
    """Apply MESH_* environment settings to the shared tool module."""
    mcp_tools.configure(
        workspace=os.environ.get("MESH_WORKSPACE") or os.getcwd(),
        read_only=_env_flag("MESH_READ_ONLY"),
        write_roots=os.environ.get("MESH_WRITE_ROOTS") or None,
        jobs_dir=os.environ.get("MESH_JOBS_DIR") or None,
        max_output_chars=os.environ.get("MESH_MAX_OUTPUT_CHARS"),
        # Public shares. This process IS the public endpoint (Funnel forwards to
        # it), so it names its own host instead of the gateway's and publishes
        # root-shaped links: /<slug>/... has no gateway to route /<node>/<slug>/.
        web_dir=os.environ.get("MESH_WEB_DIR") or None,
        mesh_user=os.environ.get("MESH_USER") or None,
        public_url=_standalone_public_url(),
        web_share_direct=True,
        max_share_bytes=os.environ.get("MESH_WEB_MAX_BYTES"),
        max_shares=os.environ.get("MESH_WEB_MAX_SHARES"),
        web_listing=os.environ.get("MESH_WEB_LISTING"),
    )


def create_mcp_server() -> RemoteHarnessMCPServer:
    """Factory used by the HTTP handler and by the test-suite."""
    configure_tools_from_env()
    return RemoteHarnessMCPServer(name="remote_harness_mcp")


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

# Endpoints that never require a token (liveness/readiness probes only).
_PUBLIC_PATHS = ("/", "/health")
# Endpoints accepting JSON-RPC POSTs.
_JSONRPC_POST_PATHS = ("/messages", "/sse", "/mcp")
# Matches a ?token=... / &token=... query value so it can be masked in logs.
_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s\"]*", re.IGNORECASE)

#: Hosts that identify this machine without naming a network interface.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Public hostname(s) an edge tunnel may present in Host/Origin.
#: Comma-separated and exact: a wildcard would re-open DNS rebinding, because
#: the attacker controls the name, and a port in the value is meaningless -
#: the edge always terminates TLS on 443.
_ALLOWED_HOSTS_ENV = "MESH_ALLOWED_HOSTS"

#: One DNS label: letters, digits, hyphens; never empty, never edging a hyphen.
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _is_public_hostname(value: str) -> bool:
    """True for a plain dotted hostname (node.tailnet.ts.net), else False.

    Rejects a wildcard, a port, a scheme, a path, spaces and single-label
    names: the value lands in a security predicate, so anything ambiguous is
    refused rather than normalised.
    """
    if not value or len(value) > 253 or value.startswith(".") or value.endswith("."):
        return False
    labels = value.split(".")
    if len(labels) < 2:
        return False
    # A bare IPv4 literal passes the label check (digits are labels too) but is
    # not a hostname this allowlist is for: it would become a share link on an
    # address that no certificate and no Funnel name covers.
    if not any(ch.isalpha() for ch in value):
        return False
    return all(_HOST_LABEL_RE.match(label) for label in labels)


def allowed_public_hosts() -> frozenset:
    """Hostnames from MESH_ALLOWED_HOSTS; empty when unset or malformed.

    The variable is read per call: it is set once for the process by the
    installer or the service, and a cache here would only hide a config error.
    """
    raw = os.environ.get(_ALLOWED_HOSTS_ENV, "") or ""
    hosts = set()
    for part in raw.split(","):
        candidate = part.strip().lower()
        if _is_public_hostname(candidate):
            hosts.add(candidate)
    return frozenset(hosts)


def _split_host_header(value: str) -> Tuple[str, str]:
    """Return (hostname, port) from a Host header; never raises."""
    raw = (value or "").strip().lower()
    if not raw:
        return "", ""
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return raw, ""
        host = raw[1:end]
        rest = raw[end + 1:]
        port = rest[1:] if rest.startswith(":") else ""
        return host, port
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        return host, port
    return raw, ""


def host_allowed(host_header: str, bind_host: str = "127.0.0.1", port: int = 8096) -> bool:
    """True when *host_header* names this loopback server and the right port.

    A DNS-rebinding page reaches ``127.0.0.1`` with its own ``Host`` (and often its
    own ``Origin``).  Requiring the loopback name and the exact port makes that
    request fail before it reaches authentication.
    """
    host, header_port = _split_host_header(host_header)
    if not host:
        return False
    if host in allowed_public_hosts():
        # The edge terminates TLS and may or may not append :443; the port
        # carries no security meaning for a name the attacker cannot own.
        return True
    allowed = set(_LOOPBACK_HOSTS)
    if bind_host:
        allowed.add(bind_host.strip().lower())
    if host not in allowed:
        return False
    if header_port and str(port) != header_port:
        return False
    return True


def origin_allowed(origin: str, bind_host: str = "127.0.0.1", port: int = 8096) -> bool:
    """True when the browser Origin is this loopback server (or absent)."""
    if not origin:
        return True
    if origin.strip().lower() == "null":
        return False
    try:
        parts = urlsplit(origin)
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if host in allowed_public_hosts():
        # A public host is only ever a browser origin over HTTPS on 443.
        if parts.scheme != "https":
            return False
        return parts.port in (None, 443)
    allowed = set(_LOOPBACK_HOSTS)
    if bind_host:
        allowed.add(bind_host.strip().lower())
    if host not in allowed:
        return False
    if parts.port is not None and int(parts.port) != int(port):
        return False
    return True


def json_content_type(value: str) -> bool:
    """True for ``application/json`` with optional parameters."""
    if not value:
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


def ensure_auth_token(token: Optional[str] = None, path: Optional[str] = None) -> str:
    """Return *token*, or generate and persist one when auth was left unset.

    An empty token used to mean "authentication disabled", which made every
    browser page on the machine a potential remote-code-execution client.  The
    server now always has a token; a generated one is written 0600 to the node's
    config directory (never to the log).
    """
    if token:
        return str(token)
    if path is None:
        from core import paths as _paths

        path = os.path.join(_paths.config_dir(), "standalone.token")
    # Reuse a token from a previous start: regenerating it on every service
    # restart would silently invalidate every configured client.
    try:
        if os.path.exists(path):
            existing = open(path, "r", encoding="utf-8").read().strip()
            if len(existing) >= 16:
                return existing
    except OSError:
        pass
    generated = secrets.token_urlsafe(32)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(generated + "\n")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("Generated a standalone MCP token at %s (mode 0600)", path)
    except Exception as exc:  # pragma: no cover - filesystem failure
        logger.error("Could not persist the standalone token: %s", exc)
    return generated


_OAUTH_PUBLIC_PATHS = frozenset({"/oauth/register", "/oauth/authorize", "/oauth/token"})


def _pkce_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _valid_redirect_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return False
    try:
        parts = urlsplit(value.strip())
    except Exception:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


class StandaloneOAuthStore(object):
    """Minimal in-memory OAuth 2.1 store for standalone MCP clients.

    Supports RFC 8414 metadata, RFC 7591 dynamic client registration, and
    Authorization Code + PKCE S256 token issuance without conflicting with the
    gateway's Google OIDC cabinet under ``/account``.
    """

    DEFAULT_CODE_TTL = 300
    DEFAULT_TOKEN_TTL = 3600
    MAX_CLIENTS = 256
    MAX_CODES = 512
    MAX_TOKENS = 512

    def __init__(self, code_ttl: int = DEFAULT_CODE_TTL, token_ttl: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self._code_ttl = max(30, int(code_ttl))
        self._explicit_token_ttl = token_ttl
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._codes: Dict[str, Dict[str, Any]] = {}
        self._tokens: Dict[str, Dict[str, Any]] = {}

    def _token_ttl(self) -> int:
        if self._explicit_token_ttl is not None:
            return max(60, int(self._explicit_token_ttl))
        try:
            return max(60, int(os.environ.get("MESH_OAUTH_TOKEN_TTL", self.DEFAULT_TOKEN_TTL)))
        except (TypeError, ValueError):
            return self.DEFAULT_TOKEN_TTL

    def _prune_locked(self, now: float) -> None:
        self._codes = {k: v for k, v in self._codes.items() if v["expires_at"] > now}
        self._tokens = {k: v for k, v in self._tokens.items() if v["expires_at"] > now}

    @staticmethod
    def metadata(base_url: str) -> Dict[str, Any]:
        issuer = base_url.rstrip("/")
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["mcp"],
        }

    def register_client(self, payload: Any) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(payload, dict):
            return 400, {
                "error": "invalid_client_metadata",
                "error_description": "Registration body must be a JSON object.",
            }
        redirect_uris = payload.get("redirect_uris")
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(_valid_redirect_uri(uri) for uri in redirect_uris)
        ):
            return 400, {
                "error": "invalid_client_metadata",
                "error_description": "redirect_uris must be a non-empty list of http(s) URLs.",
            }
        client_name = str(payload.get("client_name") or "mcp-client")[:120]
        client_id = "rhm_" + secrets.token_urlsafe(16)
        issued_at = int(time.time())
        record: Dict[str, Any] = {
            "client_id": client_id,
            "client_id_issued_at": issued_at,
            "client_name": client_name,
            "redirect_uris": [str(u).strip() for u in redirect_uris],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        with self._lock:
            if len(self._clients) >= self.MAX_CLIENTS:
                oldest_key = next(iter(self._clients))
                self._clients.pop(oldest_key, None)
            self._clients[client_id] = record
        return 201, dict(record)

    def create_authorization_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str = "mcp",
    ) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            client = self._clients.get(client_id)
            if not client:
                return None, {
                    "error": "invalid_client",
                    "error_description": "Unknown client_id.",
                }
            if redirect_uri not in client["redirect_uris"]:
                return None, {
                    "error": "invalid_request",
                    "error_description": "redirect_uri is not registered for this client.",
                }
            if code_challenge_method != "S256":
                return None, {
                    "error": "invalid_request",
                    "error_description": "PKCE code_challenge_method must be S256.",
                }
            if not isinstance(code_challenge, str) or not (43 <= len(code_challenge) <= 128):
                return None, {
                    "error": "invalid_request",
                    "error_description": "Missing or invalid PKCE code_challenge.",
                }
            code = secrets.token_urlsafe(32)
            if len(self._codes) >= self.MAX_CODES:
                oldest_code = next(iter(self._codes))
                self._codes.pop(oldest_code, None)
            self._codes[code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": scope or "mcp",
                "expires_at": now + self._code_ttl,
            }
            return code, None

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        redirect_uri: str,
    ) -> Tuple[int, Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            record = self._codes.pop(code, None)
            if not record or record["expires_at"] <= now:
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "Invalid or expired authorization code.",
                }
            if not hmac.compare_digest(
                str(client_id).encode("utf-8", "surrogateescape"),
                str(record["client_id"]).encode("utf-8", "surrogateescape"),
            ):
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "client_id does not match authorization code.",
                }
            if not hmac.compare_digest(
                str(redirect_uri).encode("utf-8", "surrogateescape"),
                str(record["redirect_uri"]).encode("utf-8", "surrogateescape"),
            ):
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "redirect_uri does not match authorization code.",
                }
            if not isinstance(code_verifier, str) or not (43 <= len(code_verifier) <= 128):
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "Invalid PKCE code_verifier.",
                }
            try:
                computed = _pkce_s256(code_verifier)
            except UnicodeEncodeError:
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "PKCE code_verifier must be ASCII.",
                }
            if not hmac.compare_digest(computed, record["code_challenge"]):
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "PKCE code_verifier mismatch.",
                }
            ttl = self._token_ttl()
            access_token = secrets.token_urlsafe(32)
            if len(self._tokens) >= self.MAX_TOKENS:
                oldest_tok = next(iter(self._tokens))
                self._tokens.pop(oldest_tok, None)
            self._tokens[access_token] = {
                "client_id": record["client_id"],
                "scope": record["scope"],
                "expires_at": now + ttl,
            }
            return 200, {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": ttl,
                "scope": record["scope"],
            }

    def validate_access_token(self, token: str) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            for candidate, meta in self._tokens.items():
                if meta["expires_at"] > now and hmac.compare_digest(
                    str(token).encode("utf-8", "surrogateescape"),
                    str(candidate).encode("utf-8", "surrogateescape"),
                ):
                    return True
        return False


class StandaloneMCPHandler(BaseHTTPRequestHandler):
    """Threaded HTTP handler exposing the MCP endpoints of the standalone server."""

    server_version = "RemoteHarnessMCPStandalone/1.0"

    # Bound slow/stalled clients (e.g. a Content-Length larger than the body it
    # sends) so a single request cannot pin a handler thread forever.
    timeout = 30

    # Set by ``run_standalone_server`` (class-level so each request thread sees it).
    server_mcp: Optional[RemoteHarnessMCPServer] = None
    auth_token: str = ""
    oauth_store: StandaloneOAuthStore = StandaloneOAuthStore()
    bind_host: str = "127.0.0.1"
    bind_port: int = 8096

    # -- plumbing ---------------------------------------------------------
    def _clean_path(self) -> str:
        try:
            path = urlparse(self.path).path
        except Exception:
            path = self.path or "/"
        if not path:
            path = "/"
        return path

    def _cors_headers(self) -> None:
        """Reflect only this loopback origin; never ``*``.

        A wildcard made every page on the machine able to read authenticated
        responses.  Requests without an Origin (curl, the node's own probes) get
        no CORS headers at all, which is correct: CORS is a browser mechanism.
        """
        origin = self.headers.get("Origin") or ""
        self.send_header("Vary", "Origin")
        if origin and origin_allowed(origin, self.bind_host, self.bind_port):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Mesh-Token")
            self.send_header("Access-Control-Max-Age", "600")

    def _request_allowed(self) -> bool:
        """Host/Origin gate that runs before auth and before any body read."""
        if not host_allowed(self.headers.get("Host", ""), self.bind_host, self.bind_port):
            return False
        origin = self.headers.get("Origin") or ""
        return origin_allowed(origin, self.bind_host, self.bind_port)

    def _send_headers(self, status: int, content_type: str = "application/json") -> None:
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", content_type)

    def _public_base(self) -> str:
        """The origin this request used, as the browser should see it."""
        host = (self.headers.get("Host") or "").strip()
        if not host:
            host = "%s:%s" % (self.bind_host, self.bind_port)
        scheme = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
        if scheme not in ("http", "https"):
            name = host.split(":", 1)[0].strip("[]").lower()
            scheme = "http" if name in _LOOPBACK_HOSTS else "https"
        return "%s://%s" % (scheme, host)

    # -- public shares ----------------------------------------------------
    #: ``/<slug>/<rest>`` - a share published by THIS node. The slug ends in a
    #: 64-bit secret, so the link itself is the credential: a browser cannot send
    #: a token to a plain URL, and the gateway relay has the same property.
    _SHARE_PATH_RE = re.compile(
        r"^/(?P<slug>[a-z0-9][a-z0-9_-]{0,63}-[0-9a-f]{16})(?P<rest>/.*)?$")

    def _share_target(self) -> Optional[Tuple[str, str]]:
        """(slug, path) when this request addresses a share, else None."""
        if _web_share is None:
            return None
        match = self._SHARE_PATH_RE.match(self._clean_path())
        if not match:
            return None
        return match.group("slug"), (match.group("rest") or "/")

    def _serve_share(self, slug: str, rest: str, head_only: bool) -> None:
        result = _web_share.handle_http_request({
            "slug": slug,
            "path": rest,
            "method": "HEAD" if head_only else "GET",
            "query": urlsplit(self.path).query,
        })
        try:
            status = int(result.get("status") or 502)
        except (TypeError, ValueError):
            status = 502
        headers = {str(k).lower(): str(v) for k, v in (result.get("headers") or {}).items()}
        body = b""
        if not head_only:
            try:
                body = base64.b64decode(result.get("body_b64") or "")
            except Exception:
                status, body = 502, b""
        self.send_response(status)
        for key in ("content-type", "content-length", "content-disposition",
                    "last-modified", "etag", "cache-control", "accept-ranges"):
            if key in headers:
                self.send_header(key, headers[key])
        if "content-length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def _send_html(self, document: str) -> None:
        body = document.encode("utf-8")
        self._send_headers(200, "text/html; charset=utf-8")
        # The page carries the token: never let a proxy, a disk cache or a
        # "shared" browser keep a copy, and never leak the URL as a referrer.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Accept")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_headers(status, "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_unauthorized(self) -> None:
        body = json.dumps(
            {"error": "unauthorized", "message": "Missing or invalid token"}
        ).encode("utf-8")
        self.send_response(401)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", 'Bearer realm="remote-harness-mcp"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _provided_token(self) -> Optional[str]:
        # Header first.  A query token is accepted only from a loopback client:
        # the standalone UI (Gemini's connector field) cannot set headers, but a
        # remote caller must never put a secret in a URL.
        token = self.headers.get("X-Mesh-Token") or None
        if token:
            return str(token)
        auth = self.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        try:
            client = (self.client_address[0] or "").strip()
        except Exception:
            client = ""
        if client in ("127.0.0.1", "::1", "localhost"):
            try:
                query = parse_qs(urlparse(self.path).query)
            except Exception:
                query = {}
            value = query.get("token", [None])[0]
            if value:
                return str(value)
        return None

    def _token_valid(self) -> bool:
        """True when the supplied token matches the configured token or a valid OAuth token.

        There is no "auth disabled" branch any more: ``run_standalone_server``
        generates a token when the operator did not set one.
        """
        try:
            provided = self._provided_token()
            if not provided:
                return False
            token = self.auth_token or ""
            # Compare raw UTF-8 bytes: hmac.compare_digest() rejects str inputs
            # containing non-ASCII characters, which would otherwise raise a
            # TypeError and drop the connection on a malformed token.
            # surrogateescape keeps any undecodable byte round-trippable.
            if token and hmac.compare_digest(
                str(provided).encode("utf-8", "surrogateescape"),
                str(token).encode("utf-8", "surrogateescape"),
            ):
                return True
            store = getattr(self, "oauth_store", None)
            if store is not None and store.validate_access_token(provided):
                return True
            return False
        except Exception:
            # Defensive: authentication must never crash the handler.
            return False

    def _authorized(self) -> bool:
        path = self._clean_path()
        if path in _PUBLIC_PATHS:
            return True
        # OAuth discovery and standalone OAuth 2.1 endpoints are unauthenticated
        # by definition (protected by Host/Origin gate and PKCE S256).
        if path.startswith("/.well-known/") or path in _OAUTH_PUBLIC_PATHS:
            return True
        return self._token_valid()

    def _read_body(self) -> bytes:
        # Streamable HTTP clients may send the JSON-RPC body chunked. BaseHTTPRequest
        # Handler does not decode that, and a chunked request then looked like an
        # empty POST - which a validator reports as "not an MCP server".
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            return self._read_chunked_body()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        try:
            return self.rfile.read(length)
        except Exception:
            return b""

    #: Upper bound for a request body: the biggest legitimate call is a tool
    #: argument blob, and an unbounded read is a memory hole.
    MAX_BODY_BYTES = 8 * 1024 * 1024

    def _read_chunked_body(self) -> bytes:
        chunks = []
        total = 0
        try:
            while True:
                line = self.rfile.readline(65536)
                if not line:
                    break
                size_text = line.strip().split(b";", 1)[0]
                if not size_text:
                    continue
                size = int(size_text, 16)
                if size <= 0:
                    # Consume the trailer so the connection stays usable.
                    while True:
                        trailer = self.rfile.readline(65536)
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                total += size
                if total > self.MAX_BODY_BYTES:
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)  # the CRLF that terminates the chunk
        except Exception:
            pass
        return b"".join(chunks)

    # -- HTTP verbs -------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        # CORS preflight is unauthenticated: it executes nothing.  It is still
        # gated by Host/Origin so a foreign page cannot use it to discover the
        # endpoint's capabilities.
        if not self._request_allowed():
            self._send_json(403, {"error": "forbidden", "message": "Host or Origin is not loopback"})
            return
        self._send_headers(204, "text/plain")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Mesh-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        if not self._request_allowed():
            self._send_json(403, {"error": "forbidden", "message": "Host or Origin is not loopback"})
            return
        share = self._share_target()
        if share is not None:
            self._serve_share(share[0], share[1], head_only=True)
            return
        if not self._authorized():
            self._send_unauthorized()
            return
        path = self._clean_path()
        if path in _PUBLIC_PATHS:
            self._send_headers(200, "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path in ("/sse", "/mcp"):
            self._send_headers(200, "text/event-stream")
            self.end_headers()
        else:
            self._send_headers(404, "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if not self._request_allowed():
            self._send_json(403, {"error": "forbidden", "message": "Host or Origin is not loopback"})
            return
        share = self._share_target()
        if share is not None:
            self._serve_share(share[0], share[1], head_only=False)
            return
        if not self._authorized():
            self._send_unauthorized()
            return

        path = self._clean_path()
        if path in _PUBLIC_PATHS:
            # A browser that followed the link from the clipboard gets the
            # connection card, not a JSON probe nobody can read.
            if self._token_valid() and landing.is_browser(self.headers):
                self._send_html(landing.render_landing(self._public_base(), self._provided_token() or ""))
                return
            # Liveness is public so the installer health poll keeps working, but
            # host vitals are only disclosed to an authenticated caller when a
            # token is configured.
            payload: Dict[str, Any] = {"status": "healthy", "mode": "standalone"}
            if self._token_valid():
                payload["vitals"] = self.server_mcp.system_vitals() if self.server_mcp else {}
            self._send_json(200, payload)
        elif path in ("/sse", "/mcp"):
            # A human opening the endpoint in a browser is not an MCP client: it
            # would sit on a stream that renders nothing. The token is already
            # validated above, so the card may show it.
            if landing.is_browser(self.headers):
                self._send_html(landing.render_landing(self._public_base(), self._provided_token() or ""))
                return
            # Which client asked for the stream is the one fact that explains a
            # connector refusing the URL; headers only, never the token.
            logger.info(
                "GET %s from ua=%r accept=%r fetch_dest=%r",
                self._clean_path(),
                (self.headers.get("User-Agent") or "")[:80],
                (self.headers.get("Accept") or "")[:80],
                (self.headers.get("Sec-Fetch-Dest") or ""),
            )
            self._stream_sse(announce_endpoint=(path == "/sse"))
        elif path == "/.well-known/oauth-authorization-server":
            self._send_json(200, self.oauth_store.metadata(self._public_base()))
        elif path == "/.well-known/oauth-protected-resource":
            base = self._public_base().rstrip("/")
            self._send_json(
                200,
                {
                    "resource": base,
                    "authorization_servers": [base],
                    "bearer_methods_supported": ["header"],
                },
            )
        elif path == "/oauth/authorize":
            self._handle_oauth_authorize()
        elif path.startswith("/.well-known/"):
            self._send_json(404, {"error": "not_found", "auth": "oauth2.1"})
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    def _send_oauth_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_headers(status, "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_oauth_authorize(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        response_type = (query.get("response_type") or [""])[0]
        client_id = (query.get("client_id") or [""])[0]
        redirect_uri = (query.get("redirect_uri") or [""])[0]
        code_challenge = (query.get("code_challenge") or [""])[0]
        code_challenge_method = (query.get("code_challenge_method") or [""])[0]
        state = (query.get("state") or [""])[0]
        scope = (query.get("scope") or ["mcp"])[0]

        if response_type != "code":
            self._send_oauth_json(
                400,
                {
                    "error": "unsupported_response_type",
                    "error_description": "Only response_type=code is supported.",
                },
            )
            return

        code, err = self.oauth_store.create_authorization_code(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
        )
        if err is not None or not code:
            self._send_oauth_json(400, err or {"error": "invalid_request"})
            return

        parts = urlsplit(redirect_uri)
        existing_pairs = parse_qs(parts.query, keep_blank_values=True)
        params: List[Tuple[str, str]] = []
        for k, vals in existing_pairs.items():
            for v in vals:
                params.append((k, v))
        params.append(("code", code))
        if state:
            params.append(("state", state))
        location = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))

        self.send_response(302)
        self._cors_headers()
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_oauth_register(self) -> None:
        if not json_content_type(self.headers.get("Content-Type", "")):
            self._send_oauth_json(
                415,
                {
                    "error": "unsupported_media_type",
                    "error_description": "Content-Type must be application/json.",
                },
            )
            return
        raw_body = self._read_body()
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            self._send_oauth_json(
                400,
                {
                    "error": "invalid_client_metadata",
                    "error_description": "Malformed JSON body.",
                },
            )
            return
        status, result = self.oauth_store.register_client(payload)
        self._send_oauth_json(status, result)

    def _handle_oauth_token(self) -> None:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        raw_body = self._read_body()
        params: Dict[str, str] = {}
        if content_type == "application/json":
            try:
                parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                if isinstance(parsed, dict):
                    params = {str(k): str(v) for k, v in parsed.items() if v is not None}
            except Exception:
                self._send_oauth_json(
                    400,
                    {"error": "invalid_request", "error_description": "Malformed JSON body."},
                )
                return
        elif content_type in ("application/x-www-form-urlencoded", ""):
            try:
                parsed_qs = parse_qs(raw_body.decode("utf-8"), keep_blank_values=False)
                params = {k: vals[0] for k, vals in parsed_qs.items() if vals}
            except Exception:
                self._send_oauth_json(
                    400,
                    {"error": "invalid_request", "error_description": "Malformed form body."},
                )
                return
        else:
            self._send_oauth_json(
                415,
                {
                    "error": "unsupported_media_type",
                    "error_description": "Content-Type must be application/x-www-form-urlencoded or application/json.",
                },
            )
            return

        if params.get("grant_type") != "authorization_code":
            self._send_oauth_json(
                400,
                {
                    "error": "unsupported_grant_type",
                    "error_description": "Only grant_type=authorization_code is supported.",
                },
            )
            return

        status, result = self.oauth_store.exchange_code(
            code=params.get("code", ""),
            code_verifier=params.get("code_verifier", ""),
            client_id=params.get("client_id", ""),
            redirect_uri=params.get("redirect_uri", ""),
        )
        self._send_oauth_json(status, result)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if not self._request_allowed():
            self._send_json(403, {"error": "forbidden", "message": "Host or Origin is not loopback"})
            return
        if not self._authorized():
            self._send_unauthorized()
            return

        path = self._clean_path()
        if path == "/oauth/register":
            self._handle_oauth_register()
            return
        if path == "/oauth/token":
            self._handle_oauth_token()
            return
        if path not in _JSONRPC_POST_PATHS:
            self._send_json(404, {"error": "not_found", "path": path})
            return
        if not json_content_type(self.headers.get("Content-Type", "")):
            # A text/plain body is a "simple request": a foreign page could send it
            # without a CORS preflight.  Requiring JSON forces a preflight, which
            # the Host/Origin gate above rejects.
            self._send_json(415, {"error": "unsupported_media_type", "message": "Content-Type must be application/json"})
            return

        raw_body = self._read_body()
        if self.server_mcp is None:
            self._send_json(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}})
            return

        logger.info(
            "POST %s from ua=%r accept=%r content_type=%r bytes=%d",
            path,
            (self.headers.get("User-Agent") or "")[:80],
            (self.headers.get("Accept") or "")[:80],
            (self.headers.get("Content-Type") or "")[:60],
            len(raw_body),
        )
        status, response = self.server_mcp.handle_jsonrpc(raw_body)
        if status == 204 or response is None:
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
            return
        if isinstance(response, (dict, list)):
            # Streamable HTTP lets the server answer a POST either with JSON or
            # with a one-frame SSE stream. Answer SSE only when the client asked
            # for that alone: a client that lists both (the usual MCP shape,
            # including Gemini) gets the JSON body it is prepared to parse.
            accept = (self.headers.get("Accept") or "").lower()
            wants_sse = "text/event-stream" in accept and "application/json" not in accept
            session_id = getattr(self.server_mcp, "session_id", "")
            if wants_sse:
                payload = json.dumps(response, ensure_ascii=False, default=str)
                frame = ("event: message\ndata: %s\n\n" % payload).encode("utf-8")
                self._send_headers(200, "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                if session_id:
                    self.send_header("Mcp-Session-Id", session_id)
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                try:
                    self.wfile.write(frame)
                except Exception:
                    pass
                return
            body = json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
            self._send_headers(status, "application/json")
            if session_id:
                self.send_header("Mcp-Session-Id", session_id)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:  # defensive: never drop the connection on a malformed handler result
            self._send_json(500, {"error": "internal_error"})

    def _query_token(self) -> Optional[str]:
        """The token from the query string, loopback clients only."""
        try:
            client = (self.client_address[0] or "").strip()
        except Exception:
            client = ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            return None
        try:
            query = parse_qs(urlparse(self.path).query)
        except Exception:
            return None
        value = query.get("token", [None])[0]
        return str(value) if value else None

    def do_DELETE(self) -> None:  # noqa: N802 - http.server API
        # Streamable HTTP clients end a session with DELETE. Answering 405 makes
        # some of them treat the server as broken.
        if not self._request_allowed():
            self._send_json(403, {"error": "forbidden", "message": "Host or Origin is not loopback"})
            return
        if not self._authorized():
            self._send_unauthorized()
            return
        path = self._clean_path()
        if path in ("/mcp", "/sse"):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
            return
        self._send_json(404, {"error": "not_found", "path": path})

    def _stream_sse(self, announce_endpoint: bool = True) -> None:
        self._send_headers(200, "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # The client posts JSON-RPC to the announced endpoint, and it learned the
        # token only from this URL. Without the token there the follow-up POST is
        # unauthenticated, and a connector reports "this is not an MCP server".
        endpoint = "/messages"
        query_token = self._query_token()
        if query_token:
            endpoint += "?token=" + quote(query_token, safe="")
        try:
            if announce_endpoint:
                self.wfile.write(("event: endpoint\ndata: %s\n\n" % endpoint).encode("utf-8"))
            self.wfile.flush()
            while True:
                time.sleep(15)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def handle_one_request(self) -> None:  # noqa: N802 - http.server API
        # A client that opens a probe and hangs up (Google does this between HEAD
        # and POST) raised ConnectionAbortedError out of the socketserver thread and
        # printed a traceback per attempt. A closed socket is not an error here.
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def log_request(self, code: Any = "-", size: Any = "-") -> None:  # noqa: N802
        # Log only the request path: the query string can carry ?token=<secret>
        # and must never reach journald / standalone.log.
        requestline = self.requestline or ""
        parts = requestline.split(" ")
        if len(parts) >= 2 and "?" in parts[1]:
            parts[1] = parts[1].split("?", 1)[0]
            requestline = " ".join(parts)
        self.log_message(
            '"%s" %s %s', requestline, str(getattr(code, "value", code)), str(size)
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        try:
            message = fmt % args
        except Exception:
            message = str(fmt)
        # Defense in depth: mask any token=... value that still reaches a log.
        message = _TOKEN_QUERY_RE.sub(r"\1***", message)
        logger.info("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), message)


#: The installer writes the node token (and the Funnel hostname) here.
_STANDALONE_ENV_FILE = "standalone.env"


def standalone_env_path() -> str:
    """Path of the per-user standalone env file the installer writes."""
    from core import paths as _paths

    return os.path.join(_paths.config_dir(), _STANDALONE_ENV_FILE)


def apply_standalone_env(path: Optional[str] = None) -> Dict[str, str]:
    """Export MESH_* keys from the standalone env file into os.environ.

    The autostart task must not carry the token in its command line, where
    every user on the machine can read it from the process list.  A real
    environment variable always wins over the file, which keeps the explicit
    --token/MESH_TOKEN flow working exactly as before.

    Returns the parsed values (empty when the file is missing).
    """
    from core import domain as _domain

    values = _domain.read_env_file(path or standalone_env_path())
    for key, value in values.items():
        if key.startswith("MESH_") and value and not os.environ.get(key):
            os.environ[key] = value
    return values


class _StandaloneHTTPServer(ThreadingHTTPServer):
    """Listen socket for the standalone MCP server.

    ``socketserver.TCPServer`` defaults to a backlog of 5. A Funnel node serves
    MCP requests and public shares from this one socket, and a page that pulls
    ten assets at once overflowed it - the browser saw connection resets while
    the node was idle. 128 is the value nginx uses by default.

    On Windows, ``SO_REUSEADDR`` allows a second process to hijack an already
    bound port without raising ``OSError``. Disabling ``allow_reuse_address``
    and setting ``SO_EXCLUSIVEADDRUSE`` guarantees a second instance fails fast.
    """

    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.allow_reuse_address = False
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_QUICK_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def parse_quick_tunnel_url(text: str) -> Optional[str]:
    """Extract a ``https://<slug>.trycloudflare.com`` URL from cloudflared output."""
    if not text:
        return None
    match = _QUICK_TUNNEL_URL_RE.search(text)
    return match.group(0) if match else None


def _add_allowed_host(hostname: str) -> None:
    candidate = (hostname or "").strip().lower()
    if not _is_public_hostname(candidate):
        return
    existing = [p.strip().lower() for p in (os.environ.get(_ALLOWED_HOSTS_ENV, "") or "").split(",") if p.strip()]
    if candidate not in existing:
        existing.append(candidate)
    os.environ[_ALLOWED_HOSTS_ENV] = ",".join(existing)


def start_quick_tunnel(
    port: int,
    token: str,
    host: str = "127.0.0.1",
    *,
    cloudflared_bin: Optional[str] = None,
    wait_timeout: float = 15.0,
) -> Tuple[Optional[subprocess.Popen], Optional[str], str]:
    """Launch a Cloudflare Quick Tunnel (``cloudflared tunnel --url``) if available.

    Returns ``(process_or_None, public_url_or_None, user_message)``.
    When ``cloudflared`` is not installed, returns ``(None, None, install_hint)``.
    """
    binary = cloudflared_bin or shutil.which("cloudflared")
    if not binary:
        hint = (
            "cloudflared is not installed or not on PATH. Install it to use --quick-tunnel:\n"
            "  Windows: winget install Cloudflare.cloudflared\n"
            "  macOS:   brew install cloudflared\n"
            "  Linux:   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
            f"Server is still listening locally on http://{host}:{int(port)}/mcp"
        )
        return None, None, hint

    target_url = f"http://{host}:{int(port)}"
    proc = subprocess.Popen(
        [binary, "tunnel", "--url", target_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    found_url: List[str] = []
    ready = threading.Event()

    def _reader() -> None:
        stream = proc.stdout
        if stream is None:
            ready.set()
            return
        try:
            for line in stream:
                url = parse_quick_tunnel_url(line)
                if url and not found_url:
                    found_url.append(url)
                    ready.set()
        except Exception:
            pass
        finally:
            ready.set()

    reader_thread = threading.Thread(target=_reader, name="rhm-cloudflared-reader", daemon=True)
    reader_thread.start()
    ready.wait(timeout=max(0.1, float(wait_timeout)))

    if found_url:
        public_url = found_url[0].rstrip("/")
        parsed = urlsplit(public_url)
        if parsed.hostname:
            _add_allowed_host(parsed.hostname)
        os.environ["MESH_PUBLIC_URL"] = public_url
        mcp_url = f"{public_url}/mcp?token={quote(token, safe='')}"
        sse_url = f"{public_url}/sse?token={quote(token, safe='')}"
        message = (
            f"Quick tunnel ready: {public_url}\n"
            f"  MCP (Streamable HTTP): {mcp_url}\n"
            f"  MCP (SSE):             {sse_url}\n"
            f"  MCP (OAuth 2.1):       {public_url}/mcp"
        )
        return proc, public_url, message

    message = (
        f"Started cloudflared for {target_url}, but no trycloudflare.com URL appeared "
        f"within {wait_timeout:.0f}s."
    )
    return proc, None, message


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="core.server",
        description="Remote Harness MCP Standalone MCP Server (localhost by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "SECURITY: --host defaults to 127.0.0.1 on purpose. This server executes\n"
            "arbitrary shell commands and reads arbitrary files. Never bind 0.0.0.0\n"
            "without setting --token/MESH_TOKEN and a firewall."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Defaults to loopback 127.0.0.1 for security; do NOT use 0.0.0.0 "
        "unless a token and firewall are configured.",
    )
    parser.add_argument("--port", type=int, default=8096, help="TCP port to listen on (default: 8096).")
    parser.add_argument(
        "--token",
        default=os.environ.get("MESH_TOKEN") or None,
        help="Shared auth token. Defaults to $MESH_TOKEN; when absent a random token is "
             "generated and written 0600 to the config directory.",
    )
    parser.add_argument(
        "--folder",
        default=None,
        help="Lock workspace and writes to a single directory (disables remote admin control and enables .git protection).",
    )
    parser.add_argument(
        "--no-shell",
        action="store_true",
        help="Disable shell execution tools (sets MESH_ENABLE_SHELL=0).",
    )
    parser.add_argument(
        "--quick-tunnel",
        action="store_true",
        help="Start a Cloudflare Quick Tunnel (cloudflared) and print the public MCP URL.",
    )
    return parser


def apply_cli_flags(args: argparse.Namespace) -> None:
    """Apply standalone CLI security/mode flags (--folder, --no-shell) to os.environ."""
    folder = getattr(args, "folder", None)
    if folder:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(str(folder))))
        if not os.path.isdir(resolved):
            raise ValueError(f"--folder path does not exist or is not a directory: {folder}")
        os.environ["MESH_WORKSPACE"] = resolved
        os.environ["MESH_WRITE_ROOTS"] = resolved
        os.environ["MESH_PROTECT_GIT"] = "1"
        os.environ["MESH_REMOTE_CONTROL"] = "0"
    if getattr(args, "no_shell", False):
        os.environ["MESH_ENABLE_SHELL"] = "0"


def run_standalone_server(
    host: str = "127.0.0.1",
    port: int = 8096,
    token: Optional[str] = None,
    *,
    quick_tunnel: bool = False,
) -> None:
    """Start the blocking standalone MCP HTTP server."""
    global _STANDALONE_PORT
    _STANDALONE_PORT = int(port)
    if token is None:
        token = os.environ.get("MESH_TOKEN", "")
    token = ensure_auth_token(token)
    mcp = create_mcp_server()
    StandaloneMCPHandler.server_mcp = mcp
    StandaloneMCPHandler.auth_token = token
    StandaloneMCPHandler.oauth_store = StandaloneOAuthStore()
    StandaloneMCPHandler.bind_host = host
    StandaloneMCPHandler.bind_port = int(port)

    httpd = _StandaloneHTTPServer((host, int(port)), StandaloneMCPHandler)
    httpd.daemon_threads = True
    tunnel_proc: Optional[subprocess.Popen] = None
    if quick_tunnel:
        tunnel_proc, _public_url, tunnel_msg = start_quick_tunnel(
            port=int(port),
            token=token,
            host="127.0.0.1" if host in ("0.0.0.0", "") else host,
        )
        print(tunnel_msg, file=sys.stderr, flush=True)
    logger.info(
        "Remote Harness MCP standalone MCP server listening on http://%s:%s/sse (auth: enabled)",
        host,
        port,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        logger.info("Standalone server stopped.")
    finally:
        if tunnel_proc is not None:
            try:
                tunnel_proc.terminate()
            except Exception:
                pass
        httpd.server_close()
        try:
            from core import usage as _usage

            _usage.flush()
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    # The token and the tunnel hostname come from the installer-written file,
    # so the autostart entry never carries a secret on its command line.
    apply_standalone_env()

    try:
        apply_cli_flags(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )
    logger.info(
        "Starting standalone MCP server on %s:%s (bind address is %s)",
        args.host,
        args.port,
        "loopback" if args.host in ("127.0.0.1", "localhost", "::1") else args.host,
    )
    if args.host == "0.0.0.0" and not args.token:
        logger.warning(
            "Bound to 0.0.0.0 without a configured token: a random token was generated. "
            "Read it from the config directory (standalone.token) or set --token."
        )
    run_standalone_server(
        host=args.host,
        port=args.port,
        token=args.token,
        quick_tunnel=bool(args.quick_tunnel),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
