import datetime as dt
import math

import pytest

import core.backtest as backtest
import core.scanner as scanner


def test_alignment_matches_evaluate_regime_at_every_valid_index():
    """core.backtest.simulate() recomputes EMA/RSI once and indexes into the
    series incrementally (for speed); this checks that indexing math against
    the already-tested evaluate_regime() (which just looks at the tail of a
    truncated list) -- if these ever disagree, the backtest doesn't actually
    match what the live scanner would have done."""
    closes = [100 + 10 * math.sin(i / 5) + i * 0.1 for i in range(80)]

    fast_series = scanner._ema(closes, scanner.FAST_EMA)
    slow_series = scanner._ema(closes, scanner.SLOW_EMA)
    rsi_series = scanner._rsi_series(closes, scanner.RSI_PERIOD)
    start_index = max(scanner.SLOW_EMA - 1, scanner.RSI_PERIOD)

    for i in range(start_index, len(closes)):
        fast_val = fast_series[i - (scanner.FAST_EMA - 1)]
        slow_val = slow_series[i - (scanner.SLOW_EMA - 1)]
        rsi_val = rsi_series[i - scanner.RSI_PERIOD]

        expected = scanner.evaluate_regime(closes[: i + 1])
        assert expected is not None
        expected_is_bull, expected_rsi = expected

        assert (fast_val > slow_val) == expected_is_bull
        assert rsi_val == pytest.approx(expected_rsi)


def _times(n: int):
    return [dt.datetime(2026, 1, 1) + dt.timedelta(minutes=i) for i in range(n)]


def test_simulate_no_trades_on_flat_prices():
    closes = [100.0] * 60
    trades = backtest.simulate(closes, _times(60), notional_usd=200.0)
    assert trades == []


def test_simulate_produces_one_closed_trade_on_a_full_cycle():
    # Flat base, then a sustained rise (bull cross + high RSI), then a
    # sustained fall (bear cross + low RSI) -- a clean single buy/close cycle.
    flat = [100.0] * 25
    rising = [100.0 + i * 2 for i in range(1, 20)]
    falling = [rising[-1] - i * 2 for i in range(1, 20)]
    closes = flat + rising + falling
    times = _times(len(closes))

    trades = backtest.simulate(closes, times, notional_usd=200.0)

    assert len(trades) == 1
    trade = trades[0]
    assert not trade.still_open
    assert trade.exit_time is not None and trade.entry_time < trade.exit_time
    assert trade.qty == pytest.approx(200.0 / trade.entry_price)
    assert trade.pnl_usd == pytest.approx((trade.exit_price - trade.entry_price) * trade.qty)


def test_simulate_leaves_a_still_open_position_if_never_closed():
    flat = [100.0] * 25
    rising = [100.0 + i * 2 for i in range(1, 20)]
    closes = flat + rising  # no reversal -- position never gets a close signal
    trades = backtest.simulate(closes, _times(len(closes)), notional_usd=200.0)

    assert len(trades) == 1
    assert trades[0].still_open
    assert trades[0].pnl_usd is None


def test_backtest_result_aggregates_correctly():
    result = backtest.BacktestResult(
        symbol="BTC/USD",
        bar_minutes=15,
        days=7,
        bars_used=100,
        trades=[
            backtest.BacktestTrade(entry_time=dt.datetime(2026, 1, 1), entry_price=100, qty=1, pnl_usd=10.0),
            backtest.BacktestTrade(entry_time=dt.datetime(2026, 1, 1), entry_price=100, qty=1, pnl_usd=-4.0),
            backtest.BacktestTrade(entry_time=dt.datetime(2026, 1, 1), entry_price=100, qty=1, still_open=True),
        ],
    )

    assert len(result.closed_trades) == 2
    assert result.total_pnl == pytest.approx(6.0)
    assert result.win_rate_pct == 50.0


def test_backtest_result_handles_no_closed_trades():
    result = backtest.BacktestResult(symbol="BTC/USD", bar_minutes=15, days=7, bars_used=100, trades=[])
    assert result.total_pnl == 0.0
    assert result.win_rate_pct is None


def test_run_backtest_clamps_days_and_wires_fetch_to_simulate(monkeypatch):
    calls = {}

    def fake_fetch(symbol, bar_minutes, days):
        calls["args"] = (symbol, bar_minutes, days)
        closes = [100.0] * 60
        times = _times(60)

        class FakeBar:
            def __init__(self, close, ts):
                self.close = close
                self.timestamp = ts

        return [FakeBar(c, t) for c, t in zip(closes, times)]

    monkeypatch.setattr(backtest, "_fetch_bars", fake_fetch)

    result = backtest.run_backtest("BTC/USD", bar_minutes=1, days=9999, notional_usd=200.0)

    assert calls["args"] == ("BTC/USD", 1, backtest.MAX_DAYS)
    assert result.bars_used == 60
    assert result.days == backtest.MAX_DAYS
