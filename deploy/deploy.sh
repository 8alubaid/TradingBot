#!/usr/bin/env bash
# Run from your machine (not on the VM) to push local code changes to the GCE VM
# and restart the service. Requires `gcloud` configured and startup-script.sh to
# have already run on the VM once.
#
# Usage: ./deploy/deploy.sh <instance-name> <zone>
set -euo pipefail

INSTANCE="${1:?Usage: deploy.sh <instance-name> <zone>}"
ZONE="${2:?Usage: deploy.sh <instance-name> <zone>}"

echo "Syncing code to $INSTANCE ($ZONE)..."

# Windows' pscp (what `gcloud compute scp` shells out to on Windows) needs the
# destination directory to already exist before copying multiple items into it
# -- unlike POSIX scp/rsync, it won't create it on the fly. Fresh each run so
# stale files from a previous deploy never linger in the staging dir.
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --command "rm -rf /tmp/tradingbot-src && mkdir -p /tmp/tradingbot-src"

gcloud compute scp --recurse \
  agents core webhook scripts deploy tests requirements.txt \
  "$INSTANCE:/tmp/tradingbot-src" --zone "$ZONE"

gcloud compute ssh "$INSTANCE" --zone "$ZONE" --command "
  set -euo pipefail
  sudo rm -rf /opt/tradingbot/agents /opt/tradingbot/core /opt/tradingbot/webhook /opt/tradingbot/scripts /opt/tradingbot/tests
  sudo cp -r /tmp/tradingbot-src/* /opt/tradingbot/
  sudo chmod +x /opt/tradingbot/deploy/*.sh
  # Only install nginx.conf if the site doesn't exist yet -- once certbot has
  # edited it in place to add the HTTPS server block, this must NOT overwrite
  # it with our plain HTTP-only source template on every subsequent deploy
  # (that would silently kill HTTPS). If you intentionally change nginx.conf,
  # re-run the certbot step from setup_gce.md afterwards.
  if [ ! -f /etc/nginx/sites-available/tradingbot ]; then
    sudo cp /opt/tradingbot/deploy/nginx.conf /etc/nginx/sites-available/tradingbot
    sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot
    sudo rm -f /etc/nginx/sites-enabled/default
  fi
  sudo nginx -t && sudo systemctl reload nginx
  sudo cp /opt/tradingbot/deploy/tradingbot.service /etc/systemd/system/tradingbot.service
  sudo cp /opt/tradingbot/deploy/tradingbot-daily-summary.service /etc/systemd/system/
  sudo cp /opt/tradingbot/deploy/tradingbot-daily-summary.timer /etc/systemd/system/
  sudo chown -R tradingbot:tradingbot /opt/tradingbot
  sudo -u tradingbot python3.11 -m venv /opt/tradingbot/.venv --upgrade-deps
  sudo -u tradingbot /opt/tradingbot/.venv/bin/pip install -q -r /opt/tradingbot/requirements.txt
  sudo systemctl daemon-reload
  sudo systemctl enable --now tradingbot.service
  sudo systemctl enable --now tradingbot-daily-summary.timer
  sudo systemctl restart tradingbot.service
  sleep 1
  sudo systemctl status tradingbot.service --no-pager -l | head -20
"

echo "Deploy complete. .env is regenerated automatically from Secret Manager each time the service starts (deploy/fetch_secrets.sh, see setup_gce.md step 5 for the secrets it needs)."
