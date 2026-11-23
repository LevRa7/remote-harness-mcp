# Installing the node in a container

A container has no systemd user session, no logon trigger and no tray icon, so the
default autostart branches cannot work there. `install.sh` therefore has a
container mode, and `docker/` carries an image that uses it.

## What container mode changes

```bash
bash install.sh --container --data-dir=/data --lang=en --domain=mesh.example.test
```

| flag | effect |
| --- | --- |
| `--container` / `--docker` | force container behaviour (no unit, no logon entry) |
| `--data-dir=<path>` | write `agent.env` there (default `/data` when it exists and is writable) |
| `--foreground` | after installing, `exec` the agent in this process |

Inside Docker, LXC, podman or Kubernetes (`/.dockerenv`, `/proc/1/cgroup`) the mode
is detected automatically, so even a plain `bash install.sh` in a Dockerfile ends
up with a node that starts instead of a unit nobody loads.

The tunnel and the standalone branch both check the mode **before** the
systemd/launchd/runit branches, so no unit file is ever written in a container.

## Image

```bash
docker build -f docker/Dockerfile -t remote-harness-mcp:latest .
```

* base: `python:3.12-slim`, plus `curl` (registration) and `tini` (PID 1, signal
  forwarding, zombie reaping);
* `VOLUME ["/data"]`: `agent.env`, `domain.env` and the cache live there;
* entrypoint: `docker/entrypoint.sh`.

## Compose

```bash
MESH_GATEWAY=mesh.example.test docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f node    # prints the MCP URL
```

The first start registers the node and writes the token to the `rhm-config`
volume; later starts reuse it, so `down`/`up` is the same node on the gateway.

## Environment

| variable | purpose |
| --- | --- |
| `MESH_GATEWAY` | shared gateway domain (required on the first start) |
| `MESH_USER` | requested node name (default: container hostname) |
| `MESH_TOKEN` | pre-issued token |
| `MESH_DATA_DIR` | volume directory the entrypoint writes to (default `/data`) |
| `MESH_FORCE_INSTALL=1` | register again even though `agent.env` exists |
| `MESH_ARGS` | extra `install.sh` arguments |
| `MESH_CONFIG_DIR` | `core/paths.py` override: configuration directory |
| `MESH_CACHE_DIR` | `core/paths.py` override: cache (jobs, captures, shares) |

`MESH_CONFIG_DIR`/`MESH_CACHE_DIR` are what make the node read the mounted
`agent.env` instead of `~/.config` inside the writable layer.

## Practical notes

* **Keep the volume.** The token is the node's identity; without the volume a
  recreated container registers a second node with a different `?user=`.
* **No published ports.** The node dials the gateway over the reverse tunnel; only
  `--mode=standalone` needs an inbound listener.
* **Logs are the UI.** The MCP URL is printed by the installer to stdout, which is
  `docker logs`. `docker/README.md` has the copy-paste commands.
* **Signals.** `exec` in the entrypoint (and in container mode) keeps the agent in
  the foreground, so `docker stop` delivers SIGTERM to the agent, not to a shell.

## Installing into an existing container

```bash
docker run --rm -it -v rhm-config:/data remote-harness-mcp:latest \
  bash install.sh --container --data-dir=/data --lang=ru --domain=mesh.example.test
```

Re-run the same command with `--force`-style semantics by setting
`MESH_FORCE_INSTALL=1` when using the image entrypoint.
