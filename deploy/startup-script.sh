#!/usr/bin/env bash
# One-time VM bootstrap: system packages, the tradingbot user/dirs, nginx site.
# Idempotent -- safe to re-run. Does NOT deploy application code; see deploy.sh
# for that (code is pushed from your machine, not baked into the VM image).
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx jq

if ! id -u tradingbot >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /usr/sbin/nologin tradingbot
fi

sudo mkdir -p /opt/tradingbot/data /opt/tradingbot/lessons
sudo chown -R tradingbot:tradingbot /opt/tradingbot

# nginx site setup happens in deploy.sh, not here -- this script runs before any
# app code (including deploy/nginx.conf) has been copied to the VM, so the file
# it would need doesn't exist yet at this point.

echo "Bootstrap complete. Next: run deploy.sh from your machine to push code and"
echo "configure nginx, then sudo certbot --nginx -d YOUR_SSLIP_DOMAIN to enable HTTPS."
