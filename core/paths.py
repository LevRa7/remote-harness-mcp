"""core/paths.py - the ONE place runtime directory names are resolved.

The project was renamed from *Antigravity Mesh* to **Remote Harness MCP**; the
deployment directory names moved with it (``antigravity-mesh`` ->
``remote-harness-mcp``). An already-installed node must survive that rename: its
token, domain file, job history, update journal and backups live under the old
names. Every runtime directory therefore resolves through this module:

* the new name wins when it exists;
* the legacy name is used when *only* it exists, so an upgrade keeps reading the
  files the previous version wrote;
* a fresh install gets the new name.

The names are declared exactly once, here, so the Python node, the updater and
the shell/PowerShell installers cannot drift apart.
"""

import os

#: Directory name in use from now on.
APP_NAME = "remote-harness-mcp"

#: Directory name shipped before the rename. Kept readable, never written to on a
#: fresh install; it disappears from a host only when the operator removes it.
LEGACY_APP_NAME = "antigravity-mesh"


def _prefer_existing(new: str, legacy: str) -> str:
    """The new path, unless only the pre-rename path exists on this host."""
    if not os.path.exists(new) and os.path.exists(legacy):
        return legacy
    return new


#: Environment variable that relocates the configuration directory. A container
#: mounts a volume, and the Windows administrator service runs under an account
#: whose home is not the operator's, so "~/.config" is not always where agent.env
#: belongs. Only a non-empty value wins; unset keeps the historical per-user
#: layout byte for byte.
CONFIG_DIR_ENV = "MESH_CONFIG_DIR"

#: Same idea for the cache (jobs, captures, shares): a container should keep them
#: on the mounted volume instead of the writable image layer.
CACHE_DIR_ENV = "MESH_CACHE_DIR"


def _env_override(name: str) -> str:
    """The absolute path named by *name*, or an empty string when unset."""
    value = os.environ.get(name, "").strip()
    if not value:
        return ""
    return os.path.abspath(os.path.expanduser(value))


def home_dir() -> str:
    """The user home as the node resolves it (``%USERPROFILE%`` on Windows)."""
    return os.path.expanduser("~")


def config_dir() -> str:
    """Configuration: ``~/.config/<app>`` (``agent.env``, ``domain.env``).

    ``MESH_CONFIG_DIR`` replaces the whole per-user location, which is what the
    container image and the Windows administrator service use (see
    docs/CONTAINER.md and docs/SERVICE_ADMIN.md).

    On Windows the same shape is used under ``%USERPROFILE%``, which is where the
    PowerShell installers have always written it.
    """
    override = _env_override(CONFIG_DIR_ENV)
    if override:
        return override
    return _prefer_existing(
        os.path.join(home_dir(), ".config", APP_NAME),
        os.path.join(home_dir(), ".config", LEGACY_APP_NAME),
    )


def etc_dir() -> str:
    """Host-level configuration on POSIX: ``/etc/<app>`` (``domain.env``, registry)."""
    return _prefer_existing(
        os.path.join("/etc", APP_NAME),
        os.path.join("/etc", LEGACY_APP_NAME),
    )


def cache_dir() -> str:
    """Per-user cache: ``~/.cache/<app>`` (jobs, shares, device captures)."""
    override = _env_override(CACHE_DIR_ENV)
    if override:
        return override
    return _prefer_existing(
        os.path.join(home_dir(), ".cache", APP_NAME),
        os.path.join(home_dir(), ".cache", LEGACY_APP_NAME),
    )


def config_file(name: str) -> str:
    return os.path.join(config_dir(), name)


def etc_file(name: str) -> str:
    return os.path.join(etc_dir(), name)


def cache_path(*parts: str) -> str:
    return os.path.join(cache_dir(), *parts)


def legacy_config_dir() -> str:
    """The pre-rename configuration directory, whether or not it exists."""
    return os.path.join(home_dir(), ".config", LEGACY_APP_NAME)
