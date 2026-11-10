#!/usr/bin/env bash
# Tests the two documented one-liners literally, over the public ts.net path,
# on a real Linux host with curl. Nothing is installed (--dry-run), and the
# bootstrap directory is removed afterwards.
set -u

HOST=racknerd-5a24bf9.merino-carob.ts.net
BASE="https://$HOST"

echo "=== 1. the Windows one-liner target (irm .../install.ps1 | iex) ==="
curl -fsSL "$BASE/install.ps1" -o /tmp/served-install.ps1 || { echo "FETCH FAILED"; exit 1; }
echo "bytes: $(wc -c < /tmp/served-install.ps1)"
echo "first 3 bytes (must not be efbbbf): $(head -c 3 /tmp/served-install.ps1 | od -An -tx1 | tr -d ' \n')"
echo "content-type: $(curl -fsSI "$BASE/install.ps1" | tr -d '\r' | grep -i '^content-type')"
echo "--- published domain in the served copy ---"
grep -n '^\s*\$PublishedDomain = ' /tmp/served-install.ps1
echo "--- first statement is param(...) ? ---"
head -3 /tmp/served-install.ps1 | sed -n '1,3p'
echo "--- param block present ---"
grep -c '^param(' /tmp/served-install.ps1

echo
echo "=== 2. the Linux/macOS/Termux one-liner, literally piped ==="
rm -rf /root/.gemini-computer-use
timeout 180 bash -c "curl -fsSL '$BASE/install.sh' | bash -s -- --dry-run < /dev/null" 2>&1 | tail -14
echo "pipeline exit: ${PIPESTATUS[0]:-?}"

echo
echo "=== 3. cleanup ==="
rm -rf /root/.gemini-computer-use /tmp/served-install.ps1
echo done
