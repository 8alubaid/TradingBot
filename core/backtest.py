"""Mechanical backtest of the EMA9/EMA21 + RSI cross strategy (core/scanner.py's
signal logic) against historical bars.

Deliberately does NOT involve the Decision Agent -- this tests whether the raw
technical setup has any edge at all, independent of the AI's judgment on top of
it. We've seen live that the Decision Agent holds on most signals; this answers
a different question ("is the signal itself any good") much faster and for free
(no Claude calls), by replaying the exact same cross-detection rules bar-by-bar
against history instead of live data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from core.scanner import FAST_EMA, RSI_PERIOD, SLOW_EMA, _client, _ema, _rsi_series

MAX_DAYS = 30  # keeps 1-minute backtests from taking too long on an unauthenticated dashboard


@dataclass
class BacktestTrade:
    entry_time: datetime
    entry_price: float
    qty: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    pnl_usd: float | None = None
    still_open: bool = False


@dataclass
class BacktestResult:
    symbol: str
    bar_minutes: int
    days: int
    bars_used: int
    trades: list[BacktestTrade] = field(default_factory=list)

    @property
    def closed_trades(self) -> list[BacktestTrade]:
        return [t for t in self.trades if not t.still_open]

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_usd or 0.0 for t in self.closed_trades)

    @property
    def win_rate_pct(self) -> float | None:
        closed = self.closed_trades
        if not closed:
            return None
        wins = sum(1 for t in closed if (t.pnl_usd or 0) >= 0)
        return round(100 * wins / len(closed), 1)


def _fetch_bars(symbol: str, bar_minutes: int, days: int) -> list:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    req = CryptoBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame(bar_minutes, TimeFrameUnit.Minute),
        start=start,
        end=end,
    )
    bars = _client().get_crypto_bars(req)
    return list(bars[symbol])


def simulate(closes: list[float], times: list[datetime], notional_usd: float) -> list[BacktestTrade]:
    """Pure logic, no I/O -- walks the same EMA9/EMA21+RSI cross rules as
    core.scanner.evaluate_regime, but at every historical bar instead of just
    the latest one, and simulates fixed-notional long-only entries/exits."""
    fast_series = _ema(closes, FAST_EMA)  # fast_series[k] == "as of" closes[FAST_EMA - 1 + k]
    slow_series = _ema(closes, SLOW_EMA)  # slow_series[k] == "as of" closes[SLOW_EMA - 1 + k]
    rsi_series = _rsi_series(closes, RSI_PERIOD)  # rsi_series[k] == "as of" closes[RSI_PERIOD + k]

    start_index = max(SLOW_EMA - 1, RSI_PERIOD)
    if start_index >= len(closes):
        return []

    trades: list[BacktestTrade] = []
    position: BacktestTrade | None = None
    prev_bull: bool | None = None

    for i in range(start_index, len(closes)):
        fast_val = fast_series[i - (FAST_EMA - 1)]
        slow_val = slow_series[i - (SLOW_EMA - 1)]
        rsi_val = rsi_series[i - RSI_PERIOD]
        is_bull = fast_val > slow_val

        if prev_bull is not None:
            crossed_bull = is_bull and not prev_bull
            crossed_bear = (not is_bull) and prev_bull

            if crossed_bull and rsi_val > 50 and position is None:
                position = BacktestTrade(
                    entry_time=times[i], entry_price=closes[i], qty=notional_usd / closes[i]
                )
            elif crossed_bear and rsi_val < 50 and position is not None:
                position.exit_time = times[i]
                position.exit_price = closes[i]
                position.pnl_usd = (closes[i] - position.entry_price) * position.qty
                trades.append(position)
                position = None

        prev_bull = is_bull

    if position is not None:
        position.still_open = True
        trades.append(position)

    return trades


def run_backtest(symbol: str, bar_minutes: int, days: int, notional_usd: float = 200.0) -> BacktestResult:
    days = max(1, min(days, MAX_DAYS))
    bars = _fetch_bars(symbol, bar_minutes, days)
    closes = [float(b.close) for b in bars]
    times = [b.timestamp for b in bars]

    trades = simulate(closes, times, notional_usd)
    return BacktestResult(symbol=symbol, bar_minutes=bar_minutes, days=days, bars_used=len(closes), trades=trades)
