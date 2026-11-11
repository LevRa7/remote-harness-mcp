"""Session policy: the single source of truth for mode + scopes.

Wave 1 only needs read-only vs agent, but the cabinet will soon expose per-agent
scopes and a live mode toggle.  Keeping that decision here (rather than in the
HTML, the gateway and the node separately) is what makes the toggle safe: the UI
cannot enable a mode the policy has not already enforced.

``mcp_tools`` still keeps its own guard for compatibility; this module is the
contract the cabinet/policy layer will use, and a test asserts the read-only set
matches the node's guard so the two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional, Tuple

MODES = ("read_only", "agent")

#: Tools that observe without changing the host.  These are the only tools a
#: read-only session may call.
READ_ONLY_TOOLS: FrozenSet[str] = frozenset({
    "mesh_status",
    "system_info",
    "system_vitals",
    "get_orchestration_skill",
    "list_dir",
    "read_file",
    "grep_search",
    "glob_find",
    "job_output",
    "job_list",
    "share_list",
    "device_info",
    "rescue_info",
    # A note in the node's config directory: it starts nothing and touches no
    # project file, so read-only sessions may still record their objective.
    "goal",
})

#: Scope -> tools it grants in ``agent`` mode.
TOOL_SCOPES: dict[str, FrozenSet[str]] = {
    "files:read": READ_ONLY_TOOLS,
    "files:write": frozenset({"write_file", "edit_file"}),
    "shell:exec": frozenset({"bash_exec", "run_job", "job_kill"}),
    "device:control": frozenset({"device_control", "device_capture", "device_messages"}),
    "shares:manage": frozenset({"share_file", "serve_dir", "unshare", "share_list"}),
    "system:update": frozenset({"mesh_update"}),
}

ALL_SCOPES: Tuple[str, ...] = tuple(TOOL_SCOPES)


@dataclass(frozen=True)
class SessionContext:
    """What one MCP connection is allowed to do.

    ``mode`` is the coarse switch the tray/cabinet shows; ``scopes`` is the fine
    list the cabinet edits.  ``workspace`` is validated by the node at call time,
    not here.
    """

    agent_id: str = "default"
    mode: str = "read_only"
    scopes: Tuple[str, ...] = ("files:read",)
    workspace: Optional[str] = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError("mode must be one of %r" % (MODES,))
        unknown = [scope for scope in self.scopes if scope not in TOOL_SCOPES]
        if unknown:
            raise ValueError("unknown scopes: %s" % ", ".join(sorted(unknown)))
        if self.mode == "read_only":
            object.__setattr__(self, "scopes", ("files:read",))


def allowed_tools(context: SessionContext, global_read_only: bool = False) -> FrozenSet[str]:
    """The tool surface this session may call.

    ``global_read_only`` (``MESH_READ_ONLY`` or the tray flag) is a hard stop: it
    wins over every scope.
    """
    if global_read_only or context.mode == "read_only":
        return READ_ONLY_TOOLS
    allowed = set()
    for scope in context.scopes:
        allowed.update(TOOL_SCOPES.get(scope, ()))
    return frozenset(allowed)


def tool_allowed(name: str, context: SessionContext, global_read_only: bool = False) -> bool:
    return name in allowed_tools(context, global_read_only)


def normalize_scopes(scopes: Iterable[str]) -> Tuple[str, ...]:
    """Drop duplicates, keep the declared order, reject unknown names."""
    result = []
    for scope in scopes:
        if scope not in TOOL_SCOPES:
            raise ValueError("unknown scope: %s" % scope)
        if scope not in result:
            result.append(scope)
    return tuple(result)
