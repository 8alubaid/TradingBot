# GCP deployment walkthrough

Single Compute Engine VM, one GCS bucket, Secret Manager for API keys, plus
Cloud Logging and Pub/Sub (already wired into the code -- this just creates
the GCP-side resources they talk to). Run these from your local machine with
the `gcloud` CLI authenticated (`gcloud auth login`) and a project selected
(`gcloud config set project YOUR_PROJECT_ID`).

## 0. Variables

```bash
export PROJECT_ID=$(gcloud config get-value project)
export ZONE=us-central1-a
export INSTANCE=tradingbot-vm
export BUCKET=${PROJECT_ID}-tradingbot
export PUBSUB_TOPIC=trading-bot-events
```

## 1. Enable APIs

```bash
gcloud services enable compute.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com logging.googleapis.com pubsub.googleapis.com
```

## 2. GCS bucket (trade + lesson storage)

```bash
gcloud storage buckets create gs://$BUCKET --location=us-central1
```

## 3. Pub/Sub topic (event-driven pipeline events)

```bash
gcloud pubsub topics create $PUBSUB_TOPIC
```

`core/events.py` publishes `signal_received`, `decision_made`, and
`trade_executed` here once `GCP_PROJECT_ID` is set on the VM -- nothing else
to configure, the topic just needs to exist.

## 4. Service account for the VM

```bash
gcloud iam service-accounts create tradingbot-vm-sa --display-name="Trading bot VM"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:tradingbot-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:tradingbot-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:tradingbot-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:tradingbot-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"
```

## 5. Secrets

Create each secret once, then set its value:

```bash
for name in anthropic-api-key alpaca-api-key alpaca-secret-key webhook-shared-secret \
            telegram-bot-token telegram-chat-id gcs-bucket-name; do
  gcloud secrets create "$name" --replication-policy=automatic
done

# set a value (repeat per secret, using your own .env values)
echo -n "sk-ant-..." | gcloud secrets versions add anthropic-api-key --data-file=-
echo -n "$BUCKET" | gcloud secrets versions add gcs-bucket-name --data-file=-
```

`webhook-shared-secret` should be the **same value already in your local `.env`**
(so the existing TradingView alert keeps working) -- don't generate a new one
unless you're also updating the alert.

## 6. Firewall

```bash
gcloud compute firewall-rules create allow-tradingbot-http \
  --allow=tcp:80,tcp:443 --target-tags=tradingbot --direction=INGRESS --source-ranges=0.0.0.0/0
```

TradingView doesn't publish a stable IP range for alert delivery, and the
dashboard needs to be reachable by you from anywhere, so this is open to the
internet -- the shared-secret check in `webhook/server.py` is the actual auth
boundary for the webhook, not the firewall. The dashboard itself is read-only
(no auth) -- fine for a class demo, but don't put anything sensitive in trade
rationale/lesson text if that matters to you.

## 7. Create the VM with a static IP

```bash
gcloud compute addresses create tradingbot-ip --region=us-central1
export VM_IP=$(gcloud compute addresses describe tradingbot-ip --region=us-central1 --format="value(address)")

gcloud compute instances create $INSTANCE \
  --zone=$ZONE \
  --machine-type=e2-small \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --tags=tradingbot \
  --address=$VM_IP \
  --service-account="tradingbot-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --scopes=cloud-platform

echo "VM external IP: $VM_IP"
echo "sslip.io domain: $(echo $VM_IP | tr '.' '-').sslip.io"
```

Note the `sslip.io` domain printed above (e.g. `34-123-45-67.sslip.io`) -- it resolves
to your VM's IP automatically, no domain purchase needed, which is what lets certbot
issue a real HTTPS certificate in the next steps. **This is also your dashboard's
public URL** once HTTPS is enabled in step 9.

## 8. Bootstrap the VM

```bash
gcloud compute scp deploy/startup-script.sh $INSTANCE:/tmp/ --zone=$ZONE
gcloud compute ssh $INSTANCE --zone=$ZONE --command="sudo mkdir -p /opt/tradingbot/deploy && sudo cp /tmp/startup-script.sh /opt/tradingbot/deploy/ && sudo chmod +x /opt/tradingbot/deploy/startup-script.sh && /opt/tradingbot/deploy/startup-script.sh"
```

## 9. Push the app code and start the service

```bash
./deploy/deploy.sh $INSTANCE $ZONE
```

## 10. Enable HTTPS

Edit `deploy/nginx.conf` locally, replacing `YOUR_SSLIP_DOMAIN` with the sslip.io
domain from step 7, then re-run `deploy.sh` (it copies `deploy/` wholesale), or just
edit it directly on the VM:

```bash
gcloud compute ssh $INSTANCE --zone=$ZONE --command="
  sudo sed -i 's/YOUR_SSLIP_DOMAIN/$(echo $VM_IP | tr '.' '-').sslip.io/' /etc/nginx/sites-available/tradingbot
  sudo nginx -t && sudo systemctl reload nginx
  sudo certbot --nginx -d $(echo $VM_IP | tr '.' '-').sslip.io --non-interactive --agree-tos -m $(gcloud config get-value account)
"
```

## 11. Point TradingView at it

Webhook URL for your TradingView alert:

```
https://<VM_IP-with-dashes>.sslip.io/webhook/tradingview
```

If you already had an alert pointed at the ngrok URL from local testing, edit
it and swap in this URL instead -- the JSON message body (with your
`WEBHOOK_SHARED_SECRET`) stays the same. See the main
[README](../README.md) for the Pine Script alert message JSON template.

**Dashboard URL** (for you, or anyone you share it with):

```
https://<VM_IP-with-dashes>.sslip.io/
```

## 12. Verify

```bash
curl https://<VM_IP-with-dashes>.sslip.io/healthz
gcloud compute ssh $INSTANCE --zone=$ZONE --command="sudo journalctl -u tradingbot -f"
```

Open the dashboard URL in a browser -- you should see the same market
overview/performance/health panels as local. Trigger a TradingView "Test"
alert and watch the log; confirm a Telegram message arrives, the trade shows
up on the dashboard, and `gs://$BUCKET/trades/` / `gs://$BUCKET/lessons/` get
new objects.

## Updating later

Code changes: `./deploy/deploy.sh $INSTANCE $ZONE` again.
Secret rotation: `gcloud secrets versions add <name> --data-file=-`, then
`gcloud compute ssh $INSTANCE --zone=$ZONE --command="sudo systemctl restart tradingbot"`.
