#!/usr/bin/env bash
# Verifies the SERVED installer end to end on a real Linux host with curl:
# fetches it over the public name, checks the baked domain, runs --dry-run
# (which downloads the bootstrap payload but installs nothing), then cleans up.
# A guard refuses to run if the served copy does not advertise --dry-run.
set -u

HOST=racknerd-5a24bf9.merino-carob.ts.net
URL="https://$HOST/install.sh"
STAGE=/tmp/mesh-served-install.sh

echo "=== 1. fetch the served installer over the public name ==="
curl -fsSL "$URL" -o "$STAGE" || { echo "FETCH FAILED"; exit 1; }
ls -l "$STAGE"
echo "sha256: $(sha256sum "$STAGE" | cut -d' ' -f1)"
echo "--- baked domain ---"
grep -n '^PUBLISHED_DOMAIN=' "$STAGE"

echo
echo "=== 2. guard: does this copy support --dry-run? ==="
if ! grep -q -- '--dry-run' "$STAGE"; then
    echo "REFUSING TO RUN: no --dry-run support, an accidental real install is possible"
    exit 1
fi
grep -n -- '--dry-run' "$STAGE" | head -3

echo
echo "=== 3. bootstrap file list this copy fetches ==="
grep -A5 'for f in core/' "$STAGE" | head -8

echo
echo "=== 4. dry run (downloads payload, installs nothing) ==="
rm -rf /root/.gemini-computer-use
( cd /tmp && timeout 180 bash "$STAGE" --dry-run < /dev/null 2>&1 | tail -25 )
echo "exit: $?"

echo
echo "=== 5. what the installer actually downloaded ==="
find /root/.gemini-computer-use -type f 2>/dev/null | sed 's|/root|~|' | sort
echo "files: $(find /root/.gemini-computer-use -type f 2>/dev/null | wc -l)"

echo
echo "=== 6. cleanup ==="
rm -rf /root/.gemini-computer-use "$STAGE"
echo done
