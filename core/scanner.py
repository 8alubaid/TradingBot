"""Autonomous market scanner: detects EMA/RSI trend-regime crosses per symbol
and feeds them through the same pipeline a TradingView alert would use.

Mirrors tradingview/ema_rsi_cross_alert.pine exactly (9/21 EMA cross confirmed
by RSI above/below 50) so a signal generated here carries the same kind of
real technical thesis -- computed here in Python instead of Pine Script, on a
schedule, so the bot doesn't have to wait on an external alert to trade.
"""

from __future__ import annotations

import logging

from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from core.config import settings
from core.models import Signal, SignalAction
from core.storage import get_indicator_regime, set_indicator_regime

logger = logging.getLogger("scanner")

FAST_EMA = 9
SLOW_EMA = 21
RSI_PERIOD = 14
BARS_NEEDED = max(SLOW_EMA, RSI_PERIOD) + 10  # headroom for a stable EMA/RSI seed


def _bars_timeframe() -> TimeFrame:
    # Read from settings at call time (not a frozen module constant) so
    # SCAN_BAR_TIMEFRAME_MINUTES can be changed via .env/redeploy without a
    # code change.
    return TimeFrame(settings.scan_bar_timeframe_minutes, TimeFrameUnit.Minute)


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for price in values[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def _rsi_series(values: list[float], period: int = RSI_PERIOD) -> list[float]:
    """RSI at every point from index `period` onward (Wilder's smoothing), i.e.
    _rsi_series(values)[k] is the RSI "as of" values[period + k]. Split out from
    _rsi() so a backtest can evaluate the strategy at every historical bar, not
    just the latest one."""
    if len(values) < period + 1:
        return []
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    series = [_rsi_from_avgs(avg_gain, avg_loss)]
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        series.append(_rsi_from_avgs(avg_gain, avg_loss))
    return series


def _rsi(values: list[float], period: int = RSI_PERIOD) -> float | None:
    series = _rsi_series(values, period)
    return series[-1] if series else None


def _client() -> CryptoHistoricalDataClient:
    return CryptoHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)


def _recent_closes(symbol: str) -> list[float]:
    req = CryptoBarsRequest(symbol_or_symbols=[symbol], timeframe=_bars_timeframe(), limit=BARS_NEEDED)
    bars = _client().get_crypto_bars(req)
    return [float(b.close) for b in bars[symbol]]


def evaluate_regime(closes: list[float]) -> tuple[bool, float] | None:
    """Pure logic, no I/O -- given a list of closes, returns (is_bull, rsi) or
    None if there isn't enough history yet. Split out from scan_symbol so the
    cross-detection math is unit-testable without hitting Alpaca."""
    fast = _ema(closes, FAST_EMA)
    slow = _ema(closes, SLOW_EMA)
    rsi = _rsi(closes)
    if not fast or not slow or rsi is None:
        return None
    n = min(len(fast), len(slow))
    return (fast[-1] > slow[-1], rsi)


def scan_symbol(symbol: str) -> Signal | None:
    """Returns a Signal if a fresh EMA cross (with RSI confirmation) was just
    detected for this symbol, else None -- doing nothing is the common case,
    safe to call on a schedule."""
    try:
        closes = _recent_closes(symbol)
    except Exception:
        logger.exception("Failed to fetch bars for %s; skipping this scan", symbol)
        return None

    result = evaluate_regime(closes)
    if result is None:
        logger.info("Not enough bar history yet for %s (%d closes); skipping", symbol, len(closes))
        return None
    is_bull_now, rsi = result

    prev_regime = get_indicator_regime(symbol)
    set_indicator_regime(symbol, "bull" if is_bull_now else "bear")

    if prev_regime is None:
        # First time seeing this symbol -- record the baseline, don't fire on it.
        return None

    price = closes[-1]
    timeframe_label = f"{settings.scan_bar_timeframe_minutes}Min"
    crossed_bull = is_bull_now and prev_regime == "bear"
    crossed_bear = (not is_bull_now) and prev_regime == "bull"

    if crossed_bull and rsi > 50:
        message = (
            f"Autonomous scan ({timeframe_label} bars): EMA{FAST_EMA} crossed above EMA{SLOW_EMA}, "
            f"RSI at {rsi:.1f} confirming bullish momentum"
        )
        logger.info("Bull cross detected for %s: %s", symbol, message)
        return Signal(symbol=symbol, action=SignalAction.BUY, price=price, timeframe=timeframe_label, message=message)

    if crossed_bear and rsi < 50:
        message = (
            f"Autonomous scan ({timeframe_label} bars): EMA{FAST_EMA} crossed below EMA{SLOW_EMA}, "
            f"RSI at {rsi:.1f} -- trend weakening, closing position"
        )
        logger.info("Bear cross detected for %s: %s", symbol, message)
        return Signal(symbol=symbol, action=SignalAction.CLOSE, price=price, timeframe=timeframe_label, message=message)

    return None
