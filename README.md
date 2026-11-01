# Trading Bot: Multi-Agent Crypto Trading System on Google Cloud

A small multi-agent trading system built as a GCP course project: signals (from
TradingView alerts or the bot's own autonomous scanner) trigger Claude-powered
reasoning about whether to trade, Alpaca executes (**paper trading only**),
Telegram (or WhatsApp) notifies you, and everything runs on a single GCP Compute
Engine VM alongside Secret Manager, Cloud Storage, Pub/Sub and Cloud Logging.
Cloud Storage holds trade history and Claude-written "lessons learned" journal
entries.

> **Paper trading only.** This is an engineering project, not a profitable
> strategy or financial advice. The live-trading gate has never been enabled, and
> results from the small paper-trading sample so far are roughly flat.

## How it works

Two ways a signal reaches the pipeline: a TradingView alert, or the bot's own
autonomous scanner (see below) -- both end up calling the exact same
`core.pipeline.process_signal()`, so everything downstream behaves identically
either way.

```
TradingView alert (Pine Script)
   -> HTTPS POST /webhook/tradingview   (FastAPI, shared-secret auth)
   -> Decision Agent (Claude via claude-agent-sdk): reasons about the signal +
      account state + recent lessons, proposes open/close/hold
   -> Risk Manager (deterministic Python, not an LLM): can only downgrade the
      decision -- max % equity per trade, daily loss kill-switch, max open
      positions, symbol allow-list, per-symbol cooldown on new entries only
      (exits are never blocked by a cooldown)
   -> Execution: places the order via alpaca-py (paper trading by default)
   -> Journal Agent (Claude): writes a markdown lessons-learned entry
   -> Notification: Telegram (or WhatsApp) message summarizing what happened
   -> Storage: every decision saved to SQLite locally + mirrored to GCS
```

Only two steps actually call Claude (Decision, Journal) -- everything else is plain,
auditable Python. This is intentional: an LLM proposes, deterministic code disposes.

## Local setup (do this before touching GCP)

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; use .venv/bin/activate on Linux/Mac
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

- `ANTHROPIC_API_KEY` -- from [console.anthropic.com](https://console.anthropic.com)
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` -- from your Alpaca **paper trading**
  account dashboard (leave `ALPACA_PAPER=true`)
- `WEBHOOK_SHARED_SECRET` -- any long random string, e.g. `openssl rand -hex 32`
- Twilio WhatsApp sandbox (see below) or leave `NOTIFIER_BACKEND` pointed at
  Telegram / leave both blank -- the bot falls back to logging notifications
  instead of sending them if credentials are missing, so it won't crash

Run tests and the server:

```bash
python -m pytest tests/
uvicorn webhook.server:app --reload
```

Send a fake TradingView alert:

```bash
curl -X POST http://127.0.0.1:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"secret":"<your WEBHOOK_SHARED_SECRET>","symbol":"BTC/USD","action":"buy","price":65000}'
```

Check: a paper order in your [Alpaca dashboard](https://app.alpaca.markets/paper/dashboard/overview),
a new file under `lessons/`, a row in `data/trades.db`, and (if configured) a Telegram or WhatsApp message.

## Dashboard

Visit `http://127.0.0.1:8000/` (or the deployed URL) for a read-only web dashboard:

- A live TradingView chart, symbol picker limited to `SYMBOL_ALLOWLIST`
- **Market overview** -- live price (Alpaca) + 24h change/market cap/volume (CoinGecko)
- **Performance** -- total realized P&L, win rate, trades today vs all-time
- **System health** -- hold/executed/rejected/error rates over a trailing 24h window
- **Recent decisions** table, filterable by symbol/status/date -- click any row
  for the full rationale plus the Claude-written lesson journal entry
- `/lessons` -- every journal entry browsable on its own, independent of the trade table

The stats bar, market overview, performance, health, and trades table all
auto-refresh every 15s (small HTML partials swapped in via `fetch`, not a full
page reload -- the chart and your symbol selection are untouched). Filtering
the trades table re-polls with your filter as query params instead of the
plain unfiltered fetch. It's served by the same FastAPI app as the webhook
(`webhook/dashboard.py`), reads straight from the existing SQLite trade history
and `lessons/` files, and needs no separate deployment or service.

**Market overview data** (`core/market_data.py`) comes from two real REST APIs:
Alpaca's Market Data API for the live tradable price, and
[CoinGecko](https://www.coingecko.com/en/api)'s free public API for 24h change,
market cap, and volume -- Alpaca doesn't expose the latter. Results are cached
server-side for 30s since CoinGecko's free tier is rate-limited; the cache means
any number of dashboard viewers only cost one upstream call per window. Either
source failing independently just blanks those fields rather than breaking the
panel.

## GCP curriculum tie-ins

Three additions map directly onto GCP course topics without changing the core
architecture -- same pipeline, same trading logic, purely additive:

- **Monitoring & Performance** -- `core/cloud_logging.py` attaches a Cloud
  Logging handler when `GCP_PROJECT_ID` is set (falls back to local-only
  logging otherwise, so it's a no-op until you deploy). The dashboard's
  **System health** panel is the local equivalent of a Monitoring dashboard --
  hold/executed/rejected/error rates computed straight from `data/trades.db`.
- **Event-Driven architecture** -- `core/events.py` publishes three events to a
  GCP **Pub/Sub** topic (`signal_received`, `decision_made`, `trade_executed`)
  from inside the existing pipeline, purely as a side effect -- nothing about
  the synchronous flow changes. Same graceful no-op pattern as Cloud
  Logging/GCS: without `GCP_PROJECT_ID` set (or before the topic exists), it
  just logs locally. Live delivery gets verified once the topic is created
  during GCP deployment (see `deploy/setup_gce.md`).
- **Choreography vs. Orchestration** -- `core/pipeline.py` is a textbook
  example of **Orchestration**: one coordinator explicitly calls Decision ->
  Risk -> Execution -> Journal -> Notify in sequence, and nothing happens
  except through it. That's deliberate for the money-moving core -- predictable
  and easy to debug. The Pub/Sub events above are what would let
  **Choreography** exist at the edges: any number of independent subscribers
  (an analytics service, a Slack bot, a second notification channel) could
  react to `trade_executed` without `pipeline.py` knowing or caring that
  they exist. No code required to "prove" this beyond the events already being
  published -- it's a property of the design, good material for the
  architecture diagram/slide.
- **Microservices** -- deliberately *not* built here. Splitting a real service
  off (e.g. notifications) would mean a second thing to deploy, monitor, and
  keep in sync for a system this size, more complexity than the assignment's
  scope calls for. Worth a slide on where the boundary *would* go (Notification
  and Journal are the most independent-serviceable pieces) rather than actually
  building it.

## Twilio WhatsApp sandbox setup (~10 min)

1. Sign up at [twilio.com](https://www.twilio.com/try-twilio), grab `Account SID` and
   `Auth Token` from the console dashboard.
2. In the console, go to Messaging -> Try it out -> Send a WhatsApp message. It shows
   a sandbox number and a `join <two-words>` code.
3. From your own WhatsApp, send that `join <two-words>` message to the sandbox number.
   You'll get a confirmation -- your number is now allowed to receive sandbox messages.
4. Set in `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `NOTIFY_WHATSAPP_TO`
   (your number, E.164 format e.g. `+15551234567`). `TWILIO_WHATSAPP_FROM` is already
   set to Twilio's shared sandbox number.

Sandbox sessions expire after a few days of inactivity (just resend the join message).
To switch to Telegram instead, set `NOTIFIER_BACKEND=telegram` and fill in
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` -- no code changes needed either way
(`core/notify.py` is a pluggable `Notifier` interface).

## TradingView alert setup

TradingView has no public trading API -- alerts POSTing JSON to a webhook is the
standard integration. In your Pine Script strategy/indicator, create an alert with
"Webhook URL" set to your server's `/webhook/tradingview` endpoint, and this as the
alert message (Message field, not the condition):

```json
{
  "secret": "<your WEBHOOK_SHARED_SECRET>",
  "symbol": "BTC/USD",
  "action": "buy",
  "price": {{close}},
  "timeframe": "{{interval}}",
  "message": "{{strategy.order.alert_message}}"
}
```

`action` must be `"buy"`, `"sell"`, or `"close"`. Use TradingView's "Test" button on
the alert to fire a one-off payload before waiting for a real market condition.

This manual template is fine for a quick connectivity test, but the Decision
Agent correctly learns to ignore signals with no real thesis behind them (see
`tradingview/ema_rsi_cross_alert.pine` for a proper indicator -- EMA crossover
confirmed by RSI momentum, builds its own webhook JSON with the actual
computed values embedded, and can fire repeatedly over time instead of once).
Paste it into TradingView's Pine Editor, add it to a chart, then create an
alert on it with Condition = "Any alert() function call", Trigger = "Once Per
Bar Close", Expiration = "Open-ended".

## Autonomous scanning (trades without any external alert)

`core/scanner.py` gives the bot its own signal source, independent of
TradingView entirely. On a schedule (`SCAN_INTERVAL_MINUTES`, default 5), it
checks every symbol in `SYMBOL_ALLOWLIST` for the same setup as the Pine Script
above -- a 9/21 EMA cross confirmed by RSI -- using bars of size
`SCAN_BAR_TIMEFRAME_MINUTES` (default 15) pulled directly from Alpaca's Market
Data API. When it detects one, it builds a `Signal` and runs it through the
exact same `process_signal()` pipeline a TradingView alert would use (Decision
Agent, Risk Manager, Execution, Journal, Notify -- no difference downstream).

**Tuning speed vs. realism:** smaller `SCAN_BAR_TIMEFRAME_MINUTES` means
crosses fire more often (good for confirming the mechanism works without a
long wait) but each one reflects a less meaningful trend change. `1`/`1` gets
you a real trade within tens of minutes for a quick demo; `15`/`5` (bar
size/scan interval) is a more realistic setting for an actual presentation.

A "cross" is a *transition*, not a level, so the scanner persists each
symbol's last-known trend regime (`indicator_state` table in `data/trades.db`)
across restarts -- otherwise every cold start would look like a fresh cross.
The first time it ever sees a symbol it just records the baseline silently;
it only fires a signal on an actual regime change with RSI confirmation, which
keeps this cheap (a no-signal scan is just a handful of free Alpaca API calls,
no Claude cost) and means most scan cycles do nothing, which is expected.

Wired into `webhook/server.py` via APScheduler -- runs in-process, no extra
systemd unit needed, starts automatically with the app (one immediate scan on
startup, then every `SCAN_INTERVAL_MINUTES`). Set
`AUTONOMOUS_SCANNING_ENABLED=false` to turn it off and rely on TradingView
alerts only.

## Backtesting the strategy

`/backtest` on the dashboard replays the same EMA9/EMA21 + RSI cross rules
(`core/backtest.py`, reusing `core/scanner.py`'s exact signal logic) against
historical Alpaca bars for a symbol/timeframe/date range you pick, simulating
fixed-notional long-only entries and exits. **No AI involved** -- this
answers "does the raw technical setup have any edge," which is a different
question from "what would the bot have done" (the Decision Agent holds on
most signals, as a real risk filter -- see `lessons/`). A consistency test
(`tests/test_backtest.py`) checks the fast bar-by-bar replay produces
identical results to calling `core.scanner.evaluate_regime()` at every point,
so the backtest is verifiably testing the same rules the live scanner uses,
not a reimplementation that could quietly drift.

Capped at `MAX_DAYS` (30) since 1-minute bars over a long window means a lot
of data pulled synchronously in one HTTP request -- fine for a class demo,
not something to lean on for serious strategy research.

## Supported coins

`SYMBOL_ALLOWLIST` in `.env` controls which symbols the risk manager will ever
approve a trade for. It's not enough for a coin to have a TradingView chart --
Alpaca has to actually list it for trading, or order placement fails. Currently
configured and verified tradable on Alpaca: `BTC/USD`, `ETH/USD`, `XRP/USD`,
`DOGE/USD`, `SOL/USD`.

**BNB and TRON are not available** -- Alpaca doesn't list either one (as of
this writing), so they can't be added no matter how the bot is configured.
This is an Alpaca platform limitation, not a bug here.

The webhook server checks `SYMBOL_ALLOWLIST` against Alpaca's live tradable
asset list on startup and logs a warning for anything that won't actually work
(`webhook/server.py`'s `_validate_symbol_allowlist`). To check by hand:

```python
from core.execution import get_tradable_crypto_symbols
print(sorted(get_tradable_crypto_symbols()))
```

## GCP deployment

See [`deploy/setup_gce.md`](deploy/setup_gce.md) for the full walkthrough: GCS bucket,
Secret Manager, the VM, HTTPS via a free `sslip.io` domain + certbot, and the systemd
services. `deploy/deploy.sh` pushes code updates after the initial setup.

## Safety

- **Paper trading by default.** `ALPACA_PAPER=true` is required unless you also set
  `I_UNDERSTAND_LIVE_TRADING=true` -- the app refuses to start otherwise
  (`core/config.py`).
- **Risk manager is deterministic, not AI.** It can only reject or approve what the
  Decision Agent proposed, never invent or upsize a trade.
- This is a class project / paper-trading demo, not investment advice, and hasn't
  been through the kind of review real trading infrastructure needs.

## Known limitations (things to call out if asked in class, or to extend later)

- **P&L is approximate.** `pnl_usd` on a closed position is the position's
  *unrealized* P/L captured immediately before the close order is sent, not exact
  fill-price accounting from Alpaca's activity log. Good enough to drive the daily
  loss kill-switch and a demo P&L number; not audit-grade.
- **The Decision Agent doesn't call live tools.** Account state and recent lessons
  are fetched in Python and embedded in the prompt rather than given as callable
  MCP tools, which keeps the reasoning step simple, fast, and easy to debug/demo --
  but means the agent can't, e.g., ask a follow-up question about a specific past
  trade.
- **Single-process storage.** SQLite + per-file GCS objects work fine for a
  single-VM demo; wouldn't scale to concurrent writers without more care.
- **No backtesting.** The bot only reacts to live TradingView alerts; there's no
  historical replay/backtest harness here.
