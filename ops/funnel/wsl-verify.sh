#!/usr/bin/env bash
# End-to-end check of the documented one-liner through the PUBLIC ts.net path
# (this is what a phone on a mobile network does). Read-only apart from the
# bootstrap download the installer itself performs, which is then cleaned up.
set -u
HOST=racknerd-5a24bf9.merino-carob.ts.net
BASE="https://$HOST"

echo "=== 1. DNS as the phone would see it ==="
getent hosts "$HOST" 2>/dev/null || python3 -c "import socket;print(socket.gethostbyname('$HOST'))"

echo
echo "=== 2. fetch the documented one-liner target ==="
rm -rf "$HOME/.gemini-computer-use"
python3 - "$BASE/install.sh" <<'PY'
import sys, urllib.request
url = sys.argv[1]
with urllib.request.urlopen(url, timeout=40) as r:
    data = r.read()
    print("HTTP", r.status, "bytes", len(data))
open("/tmp/mesh-install.sh", "wb").write(data)
PY

echo
echo "=== 3. domain baked into the served copy ==="
grep -n '^PUBLISHED_DOMAIN=' /tmp/mesh-install.sh

echo
echo "=== 4. installer dry run (downloads payload, installs nothing) ==="
bash /tmp/mesh-install.sh --dry-run 2>&1 | grep -Ei '^(Tried|Пробовали)|Domain|MCP URL|\[DRY-RUN\]' | head -20

echo
echo "=== 5. bootstrap payload over the public path ==="
python3 - "$BASE" <<'PY'
import sys, urllib.request, urllib.error
base = sys.argv[1]
files = ["core/agent.py", "core/server.py", "core/mcp_tools.py", "core/web_share.py",
         "core/domain.py", "core/vitals.py", "core/updater.py", "core/version.py",
         "core/__init__.py", "skills/orchestrator.md"]
bad = 0
for f in files:
    try:
        with urllib.request.urlopen(base + "/" + f, timeout=30) as r:
            n = len(r.read())
        print("  %-28s %s %d bytes" % (f, r.status, n))
    except urllib.error.HTTPError as e:
        print("  %-28s %s FAILED" % (f, e.code)); bad += 1
    except Exception as e:
        print("  %-28s ERR %s" % (f, e)); bad += 1
print("FAILURES:", bad)
PY

echo
echo "=== 6. what the installer actually downloaded into the bootstrap dir ==="
find "$HOME/.gemini-computer-use" -type f 2>/dev/null | sed "s|$HOME|~|" | sort

echo
echo "=== cleanup ==="
rm -rf "$HOME/.gemini-computer-use" /tmp/mesh-install.sh
echo done
