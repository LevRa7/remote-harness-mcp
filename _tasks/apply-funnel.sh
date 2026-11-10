#!/usr/bin/env bash
# Adds the Funnel ingress vhost for the ts.net name to the mesh nginx config.
# Idempotent: repeated runs change nothing. Verifies with nginx -t before reload.
set -u

F=/etc/nginx/sites-available/tailscale-mesh.conf
SRC=/tmp/funnel-mesh.conf
MARK="listen 127.0.0.1:8098;"

echo "=== before: occurrences of 8098 in config: $(grep -cF "$MARK" "$F" || true)"
echo "=== nginx running? ==="
systemctl is-active nginx
echo "=== nginx master config test ==="
nginx -t 2>&1 | tail -3

if grep -qF "$MARK" "$F"; then
    echo "=== block already present, not appending"
else
    TS=$(date +%Y%m%d-%H%M%S)
    BK=/root/backups/antigravity-mesh/nginx-$TS
    mkdir -p "$BK"
    cp -a "$F" "$BK/"
    tr -d '\r' < "$SRC" >> "$F"
    echo "=== appended block (backup: $BK)"
fi

echo "=== config test after edit ==="
if ! nginx -t 2>&1 | tail -3; then
    echo "!!! nginx -t FAILED - reloading nothing"
    exit 1
fi

systemctl reload nginx
echo "=== reload rc=$? ==="
sleep 1
systemctl is-active nginx

echo "=== listening on 8098 ==="
ss -tlnp | grep -F ':8098' || echo "(not listening)"

echo "=== http checks through the new vhost ==="
for p in /install.sh /install.ps1 /core/domain.py /health; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: racknerd-5a24bf9.merino-carob.ts.net' "http://127.0.0.1:8098$p")
    echo "  $p -> $code"
done

echo "=== last lines of the vhost file ==="
tail -8 "$F"
