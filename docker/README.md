# Container installation

The node runs in a container without systemd, cron or a tray icon: `install.sh
--container` writes `agent.env` to a mounted volume, and the image then keeps the
agent in the foreground.

## Quick start (docker compose)

From the repository root:

```bash
MESH_GATEWAY=mesh.example.test docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f node     # prints the MCP URL
```

The URL printed in the log is the one to paste into Google Gemini
(<https://gemini.google.com/customize/connectors>). The token lives in the
`rhm-config` volume, so `down`/`up` is the same node, not a new one.

## Plain docker

```bash
docker build -f docker/Dockerfile -t remote-harness-mcp:latest .
docker run -d --name rhm-node --restart unless-stopped \
  -e MESH_GATEWAY=mesh.example.test \
  -e MESH_USER=my-node \
  -v rhm-config:/data \
  remote-harness-mcp:latest
docker logs rhm-node | grep -A4 'MCP SERVER'
```

## Installing into an existing container

`install.sh` detects Docker, LXC, podman and Kubernetes through `/.dockerenv` and
`/proc/1/cgroup`, so a plain run inside such a container already skips the systemd
unit. The explicit flags are:

| flag | meaning |
| --- | --- |
| `--container` / `--docker` | force container behaviour on any host (also `docker exec`, CI) |
| `--data-dir=<path>` | where `agent.env` is written (default `/data` when it exists, else `~/.config/remote-harness-mcp`) |
| `--foreground` | after installing, exec the agent in this process (what the image entrypoint does) |

```bash
docker run --rm -it -v rhm-config:/data remote-harness-mcp:latest \
  bash install.sh --container --data-dir=/data --lang=ru --domain=mesh.example.test
```

## Environment variables

| variable | purpose |
| --- | --- |
| `MESH_CONFIG_DIR` | `core/paths.py` override: where `agent.env`, `domain.env` and the account DB live |
| `MESH_CACHE_DIR` | same for the cache (jobs, captures, shares) |
| `MESH_DATA_DIR` | entrypoint: the volume directory (default `/data`) |
| `MESH_FORCE_INSTALL` | `1` re-registers even though `agent.env` exists |
| `MESH_ARGS` | extra `install.sh` arguments, e.g. `--port=8096` |

## Recreating a container

The agent's token is the node's identity. Keep the volume:

```bash
docker rm -f rhm-node && docker run ... -v rhm-config:/data ...
```

Losing the volume means registering a second node with a different `?user=`.
