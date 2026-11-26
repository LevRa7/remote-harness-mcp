#!/usr/bin/env bash
# Temporarily switches the gateway's public domain to the Tailscale name:
#   * domain.env        -> the one place the domain lives
#   * served installers -> PUBLISHED_DOMAIN / $PublishedDomain, the two lines
#                          deploy_gateway.sh rewrites
# Only those lines change; the deployed installer versions are left as they are.
set -u

NEW_HOST=racknerd-5a24bf9.merino-carob.ts.net
NEW_URL="https://$NEW_HOST"
TS=$(date +%Y%m%d-%H%M%S)
BK=/root/backups/antigravity-mesh/domain-switch-$TS
mkdir -p "$BK"

echo "=== backup -> $BK ==="
cp -a /etc/antigravity-mesh/domain.env "$BK/domain.env"
cp -a /var/www/antigravity-mesh/install.sh "$BK/install.sh"
cp -a /var/www/antigravity-mesh/install.ps1 "$BK/install.ps1"

echo "=== 1. domain.env ==="
printf 'MESH_PUBLIC_URL=%s\n' "$NEW_URL" > /etc/antigravity-mesh/domain.env
chmod 644 /etc/antigravity-mesh/domain.env
cat /etc/antigravity-mesh/domain.env

echo "=== 2. published installers ==="
sed -i "s|^PUBLISHED_DOMAIN=.*|PUBLISHED_DOMAIN=\"$NEW_HOST\"|" /var/www/antigravity-mesh/install.sh
sed -i "s|^\([[:space:]]*\)\$PublishedDomain = .*|\1\$PublishedDomain = \"$NEW_HOST\"|" /var/www/antigravity-mesh/install.ps1
grep -n '^PUBLISHED_DOMAIN=' /var/www/antigravity-mesh/install.sh
grep -n '^\s*\$PublishedDomain = ' /var/www/antigravity-mesh/install.ps1
echo "placeholder marker still present (must be 1 each):"
grep -c '__MESH_DOMAIN__' /var/www/antigravity-mesh/install.sh /var/www/antigravity-mesh/install.ps1
echo "BOM check on served install.ps1 (must NOT be efbbbf):"
head -c 3 /var/www/antigravity-mesh/install.ps1 | od -An -tx1 | tr -d ' \n'; echo

echo "=== 3. restart gateway ==="
systemctl restart agy-gateway.service
sleep 3
systemctl is-active agy-gateway.service

echo "=== 4. gateway self-report ==="
curl -s -m 15 http://127.0.0.1:8096/health | head -c 220; echo
echo "=== 5. served installer over nginx (loopback) ==="
curl -s -m 15 -H "Host: $NEW_HOST" http://127.0.0.1:8098/install.sh | grep -n '^PUBLISHED_DOMAIN='
