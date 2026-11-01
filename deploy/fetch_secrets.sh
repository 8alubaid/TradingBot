#!/usr/bin/env bash
# Pulls secrets from GCP Secret Manager (via the VM's attached service account,
# no key file needed) and writes /opt/tradingbot/.env. Run automatically as an
# ExecStartPre before the tradingbot service starts -- restart the service after
# rotating a secret to pick up the new value.
#
# Requires: the VM's service account has roles/secretmanager.secretAccessor,
# and secrets below exist (see setup_gce.md for the `gcloud secrets create` commands).
set -euo pipefail

METADATA="http://metadata.google.internal/computeMetadata/v1"
PROJECT_ID=$(curl -s -H "Metadata-Flavor: Google" "$METADATA/project/project-id")
TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
  "$METADATA/instance/service-accounts/default/token" | jq -r .access_token)

fetch_secret() {
  local secret_id="$1"
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${secret_id}/versions/latest:access" \
    | jq -r '.payload.data' | base64 -d
}

declare -A SECRET_MAP=(
  [ANTHROPIC_API_KEY]=anthropic-api-key
  [ALPACA_API_KEY]=alpaca-api-key
  [ALPACA_SECRET_KEY]=alpaca-secret-key
  [WEBHOOK_SHARED_SECRET]=webhook-shared-secret
  [TELEGRAM_BOT_TOKEN]=telegram-bot-token
  [TELEGRAM_CHAT_ID]=telegram-chat-id
  [GCS_BUCKET_NAME]=gcs-bucket-name
)

ENV_FILE=/opt/tradingbot/.env
umask 077
: > "$ENV_FILE"
for env_name in "${!SECRET_MAP[@]}"; do
  secret_id="${SECRET_MAP[$env_name]}"
  value=$(fetch_secret "$secret_id")
  echo "${env_name}=${value}" >> "$ENV_FILE"
done

# Non-secret config. Edit here (then `sudo systemctl restart tradingbot`) rather
# than in Secret Manager -- nothing below is sensitive.
cat >> "$ENV_FILE" <<EOF
GCP_PROJECT_ID=${PROJECT_ID}
ALPACA_PAPER=true
I_UNDERSTAND_LIVE_TRADING=false
NOTIFIER_BACKEND=telegram
MAX_POSITION_PCT_OF_EQUITY=0.05
DAILY_LOSS_LIMIT_PCT=0.03
MAX_OPEN_POSITIONS=3
SYMBOL_ALLOWLIST=BTC/USD,ETH/USD,XRP/USD,DOGE/USD,SOL/USD
SYMBOL_COOLDOWN_SECONDS=300
DATABASE_PATH=/opt/tradingbot/data/trades.db
LOCAL_LESSONS_DIR=/opt/tradingbot/lessons
PUBSUB_EVENTS_TOPIC=trading-bot-events
AUTONOMOUS_SCANNING_ENABLED=true
# DEMO SETTING: 1-minute bars/scans so a real cross shows up quickly. For an
# actual presentation, raise both back up (e.g. 15/5) -- see README.
SCAN_INTERVAL_MINUTES=1
SCAN_BAR_TIMEFRAME_MINUTES=1
EOF

chmod 600 "$ENV_FILE"
