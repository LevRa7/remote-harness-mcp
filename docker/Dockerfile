# syntax=docker/dockerfile:1
# ==============================================================================
#  Remote Harness MCP - node image
# ==============================================================================
#
#  Build from the repository root:
#      docker build -f docker/Dockerfile -t remote-harness-mcp:latest .
#
#  The image is the node itself: no systemd, no cron, no tray. docker/entrypoint.sh
#  registers the node on the shared gateway on the first start (writing agent.env
#  to the /data volume) and then runs the agent in the foreground.
# ==============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MESH_DATA_DIR=/data \
    MESH_CONFIG_DIR=/data \
    MESH_CACHE_DIR=/data/cache

# curl is what install.sh uses for registration; tini reaps zombies and forwards
# signals to the agent, which is why the agent can then receive SIGTERM.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/remote-harness-mcp
COPY . .
RUN python3 -m pip install --no-cache-dir websockets \
 && chmod +x install.sh docker/entrypoint.sh

VOLUME ["/data"]

# tini is PID 1: it forwards SIGTERM/SIGINT to the entrypoint's agent and reaps
# the children a tool call leaves behind.
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/bash", "/opt/remote-harness-mcp/docker/entrypoint.sh"]
