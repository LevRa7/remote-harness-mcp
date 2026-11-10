#!/usr/bin/env bash
# Repoints the public Funnel root on 443 from the app (8096) to nginx (8098),
# which serves the installers, the bootstrap payload and the MCP surface.
# The /tg and /brother path handlers must survive this.
set -u

echo "=== serve JSON before ==="
tailscale serve status --json | tr -d ' \n' | head -c 900; echo

echo "=== repointing / on :443 -> http://127.0.0.1:8098 ==="
tailscale funnel --bg --https=443 http://127.0.0.1:8098 2>&1
echo "rc=$?"
sleep 2

echo "=== serve status after ==="
tailscale serve status 2>&1

echo "=== funnel status after ==="
tailscale funnel status 2>&1 | head -4
