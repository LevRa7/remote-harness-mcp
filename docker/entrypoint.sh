#!/usr/bin/env bash
# ==============================================================================
#  Remote Harness MCP - container entrypoint
# ==============================================================================
#
#  The image carries the node code; this script decides whether the node still has
#  to be registered and then replaces itself with the agent, so the agent is the
#  container's foreground process and docker stop delivers SIGTERM to it.
#
#  Environment:
#    MESH_GATEWAY   shared gateway domain (required on the first start)
#    MESH_USER      requested node name (default: the container hostname)
#    MESH_TOKEN     pre-issued token (optional; the gateway issues one otherwise)
#    MESH_DATA_DIR  where agent.env lives (default /data, a declared VOLUME)
#    MESH_LANG      en (default) or ru
#    MESH_FORCE_INSTALL=1  register again even though agent.env exists
#    MESH_ARGS      extra install.sh arguments, e.g. "--port=8096"
set -eu

DATA_DIR="${MESH_DATA_DIR:-/data}"
LANG_CODE="${MESH_LANG:-en}"
INSTALL_DIR="${RHM_HOME:-/opt/remote-harness-mcp}"
CONFIG_FILE="$DATA_DIR/agent.env"

mkdir -p "$DATA_DIR"
# core/paths.py resolves MESH_CONFIG_DIR before "~/.config": the agent then reads
# the very agent.env this script writes, and survives a container recreation.
export MESH_CONFIG_DIR="$DATA_DIR"
# The node resolves its cache through the same rule (jobs, captures, shares).
export MESH_CACHE_DIR="${MESH_CACHE_DIR:-$DATA_DIR/cache}"

if [ -s "$CONFIG_FILE" ] && [ "${MESH_FORCE_INSTALL:-0}" != "1" ]; then
    echo "[OK] Reusing the configuration in $CONFIG_FILE (set MESH_FORCE_INSTALL=1 to re-register)."
else
    set -- --container --data-dir="$DATA_DIR" --lang="$LANG_CODE"
    [ -n "${MESH_GATEWAY:-}" ] && set -- "$@" --domain="$MESH_GATEWAY"
    [ -n "${MESH_USER:-}" ] && set -- "$@" --user="$MESH_USER"
    [ -n "${MESH_TOKEN:-}" ] && set -- "$@" --token="$MESH_TOKEN"
    if [ -n "${MESH_ARGS:-}" ]; then
        # Deliberately word-split: MESH_ARGS is an argument list, not one value.
        # shellcheck disable=SC2086
        set -- "$@" $MESH_ARGS
    fi
    cd "$INSTALL_DIR"
    bash ./install.sh "$@"
fi

cd "$INSTALL_DIR"
echo "[OK] Starting the node agent (container mode)."
exec "${PYTHON:-python3}" -u -m core.agent
