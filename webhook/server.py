"""FastAPI webhook that receives TradingView alerts and runs them through the pipeline.

TradingView alerts can't do real authentication, so the alert's JSON message body
must include a "secret" field matching WEBHOOK_SHARED_SECRET. See README for the
Pine Script alert message template.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from core.cloud_logging import setup_cloud_logging
from core.config import settings
from core.events import publish_event
from core.execution import get_tradable_crypto_symbols
from core.models import Signal, SignalAction
from core.pipeline import process_signal
from core.scanner import scan_symbol
from webhook.dashboard import router as dashboard_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

app = FastAPI(title="Abu Khalid Trading Bot Webhook")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(dashboard_router)

_scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def _setup_cloud_logging() -> None:
    setup_cloud_logging()


@app.on_event("startup")
async def _validate_symbol_allowlist() -> None:
    try:
        tradable = get_tradable_crypto_symbols()
    except Exception:
        logger.exception("Could not fetch Alpaca's tradable symbol list at startup; skipping validation")
        return

    unsupported = [s for s in settings.symbol_list if s not in tradable]
    if unsupported:
        logger.warning(
            "SYMBOL_ALLOWLIST includes symbols Alpaca doesn't currently support and will "
            "never be tradable: %s. Orders for these will fail at execution time.",
            unsupported,
        )


async def _run_scan_cycle() -> None:
    """One pass over every allowlisted symbol -- the common case is zero
    signals generated (a cross is a relatively rare event), so this is cheap
    and safe to run frequently."""
    for symbol in settings.symbol_list:
        try:
            signal = scan_symbol(symbol)
        except Exception:
            logger.exception("Autonomous scan failed for %s", symbol)
            continue
        if signal is None:
            continue

        logger.info("Autonomous scan generated a %s signal for %s", signal.action.value, symbol)
        publish_event(
            "signal_received",
            {"symbol": signal.symbol, "action": signal.action.value, "price": signal.price, "source": "autonomous_scanner"},
        )
        await process_signal(signal)


@app.on_event("startup")
async def _start_autonomous_scanner() -> None:
    if not settings.autonomous_scanning_enabled:
        logger.info("Autonomous scanning disabled (AUTONOMOUS_SCANNING_ENABLED=false)")
        return

    _scheduler.add_job(
        _run_scan_cycle,
        trigger=IntervalTrigger(minutes=settings.scan_interval_minutes),
        id="autonomous_scanner",
        next_run_time=datetime.now(),  # also fire once immediately on startup
        max_instances=1,  # never let a slow cycle overlap with the next one
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Autonomous scanner started: every %s minute(s) for %s",
        settings.scan_interval_minutes,
        settings.symbol_list,
    )


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "paper_trading": settings.alpaca_paper}


@app.post("/webhook/tradingview")
async def tradingview_webhook(request: Request) -> dict:
    payload = await request.json()

    received = payload.get("secret")
    if received != settings.webhook_shared_secret:
        # Diagnostic only -- never logs the actual secret values, just enough to
        # tell whitespace/truncation/wrong-value apart without exposing either string.
        logger.warning(
            "Webhook auth failed: received len=%s expected len=%s "
            "stripped_match=%s received_hash=%s expected_hash=%s",
            len(received) if isinstance(received, str) else type(received).__name__,
            len(settings.webhook_shared_secret),
            isinstance(received, str) and received.strip() == settings.webhook_shared_secret.strip(),
            hashlib.sha256(received.encode()).hexdigest()[:8] if isinstance(received, str) else "n/a",
            hashlib.sha256(settings.webhook_shared_secret.encode()).hexdigest()[:8],
        )
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        signal = Signal(
            symbol=payload["symbol"],
            action=SignalAction(str(payload["action"]).lower()),
            price=payload.get("price"),
            timeframe=payload.get("timeframe"),
            message=payload.get("message"),
            raw=payload,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid payload: {exc}") from exc

    logger.info("Received signal: %s %s @ %s", signal.action, signal.symbol, signal.price)
    publish_event("signal_received", {"symbol": signal.symbol, "action": signal.action.value, "price": signal.price})

    record = await process_signal(signal)
    return {"trade_id": record.id, "status": record.status.value}
