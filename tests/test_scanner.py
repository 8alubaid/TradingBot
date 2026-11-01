import pytest

import core.scanner as scanner
from core.config import settings
from core.models import DecisionAction, SignalAction


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "trades.db"))


def test_ema_of_constant_series_equals_that_constant():
    values = [100.0] * 30
    result = scanner._ema(values, 9)
    assert result[-1] == pytest.approx(100.0)


def test_ema_too_short_returns_empty():
    assert scanner._ema([1.0, 2.0], 9) == []


def test_rsi_all_gains_is_100():
    values = [float(i) for i in range(1, 20)]  # strictly increasing
    assert scanner._rsi(values) == pytest.approx(100.0)


def test_rsi_all_losses_is_near_zero():
    values = [float(i) for i in range(20, 1, -1)]  # strictly decreasing
    assert scanner._rsi(values) < 1.0


def test_rsi_too_short_returns_none():
    assert scanner._rsi([1.0, 2.0, 3.0]) is None


def test_evaluate_regime_insufficient_history_returns_none():
    assert scanner.evaluate_regime([1.0, 2.0, 3.0]) is None


def test_scan_symbol_first_call_records_baseline_and_returns_none(monkeypatch):
    monkeypatch.setattr(scanner, "_recent_closes", lambda symbol: [100.0] * 40)
    monkeypatch.setattr(scanner, "evaluate_regime", lambda closes: (True, 60.0))

    result = scanner.scan_symbol("BTC/USD")

    assert result is None
    assert scanner.get_indicator_regime("BTC/USD") == "bull"


def test_scan_symbol_fires_buy_on_confirmed_bull_cross(monkeypatch):
    monkeypatch.setattr(scanner, "_recent_closes", lambda symbol: [100.0] * 40)
    monkeypatch.setattr(scanner, "set_indicator_regime", scanner.set_indicator_regime)
    scanner.set_indicator_regime("ETH/USD", "bear")

    monkeypatch.setattr(scanner, "evaluate_regime", lambda closes: (True, 65.0))
    result = scanner.scan_symbol("ETH/USD")

    assert result is not None
    assert result.action == SignalAction.BUY
    assert result.symbol == "ETH/USD"
    assert "bullish" in result.message


def test_scan_symbol_fires_close_on_confirmed_bear_cross(monkeypatch):
    scanner.set_indicator_regime("SOL/USD", "bull")
    monkeypatch.setattr(scanner, "_recent_closes", lambda symbol: [100.0] * 40)
    monkeypatch.setattr(scanner, "evaluate_regime", lambda closes: (False, 40.0))

    result = scanner.scan_symbol("SOL/USD")

    assert result is not None
    assert result.action == SignalAction.CLOSE
    assert "weakening" in result.message


def test_scan_symbol_no_signal_without_rsi_confirmation(monkeypatch):
    scanner.set_indicator_regime("DOGE/USD", "bear")
    monkeypatch.setattr(scanner, "_recent_closes", lambda symbol: [100.0] * 40)
    # Regime flipped bullish, but RSI doesn't confirm (<=50).
    monkeypatch.setattr(scanner, "evaluate_regime", lambda closes: (True, 45.0))

    result = scanner.scan_symbol("DOGE/USD")

    assert result is None


def test_scan_symbol_no_signal_when_regime_unchanged(monkeypatch):
    scanner.set_indicator_regime("XRP/USD", "bull")
    monkeypatch.setattr(scanner, "_recent_closes", lambda symbol: [100.0] * 40)
    monkeypatch.setattr(scanner, "evaluate_regime", lambda closes: (True, 70.0))

    result = scanner.scan_symbol("XRP/USD")

    assert result is None


def test_scan_symbol_handles_fetch_failure_gracefully(monkeypatch):
    def boom(symbol):
        raise RuntimeError("Alpaca is down")

    monkeypatch.setattr(scanner, "_recent_closes", boom)
    assert scanner.scan_symbol("BTC/USD") is None
