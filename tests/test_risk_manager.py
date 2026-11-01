from types import SimpleNamespace

import pytest

import core.risk_manager as risk_manager
from core.config import settings
from core.execution import AccountState
from core.models import DecisionAction, Signal, SignalAction, TradeDecision


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    risk_manager._last_trade_time.clear()
    yield
    risk_manager._last_trade_time.clear()


@pytest.fixture(autouse=True)
def _no_daily_loss_halt(monkeypatch):
    monkeypatch.setattr(risk_manager, "get_todays_realized_pnl", lambda: 0.0)


@pytest.fixture(autouse=True)
def _default_symbol_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "symbol_allowlist", "BTC/USD,ETH/USD")
    monkeypatch.setattr(settings, "max_position_pct_of_equity", 0.05)
    monkeypatch.setattr(settings, "max_open_positions", 3)
    monkeypatch.setattr(settings, "symbol_cooldown_seconds", 300)


def _signal(symbol="BTC/USD", action=SignalAction.BUY) -> Signal:
    return Signal(symbol=symbol, action=action, price=65000.0)


def _decision(symbol="BTC/USD", action=DecisionAction.OPEN, notional=1000.0) -> TradeDecision:
    return TradeDecision(
        signal=_signal(symbol),
        action=action,
        side=SignalAction.BUY,
        notional_usd=notional,
        rationale="test",
    )


def _account(equity=100_000.0, cash=100_000.0, buying_power=100_000.0, positions=None) -> AccountState:
    return AccountState(equity=equity, cash=cash, buying_power=buying_power, positions=positions or [])


def test_hold_is_always_approved():
    decision = _decision(action=DecisionAction.HOLD, notional=None)
    result = risk_manager.evaluate(decision, _account())
    assert result.approved_by_risk_manager is True


def test_symbol_not_in_allowlist_is_rejected():
    decision = _decision(symbol="DOGE/USD")
    result = risk_manager.evaluate(decision, _account())
    assert result.approved_by_risk_manager is False
    assert "allow-list" in result.rejection_reason


def test_open_within_limits_is_approved():
    decision = _decision(notional=1000.0)  # 1% of 100k equity
    result = risk_manager.evaluate(decision, _account())
    assert result.approved_by_risk_manager is True


def test_open_exceeding_max_pct_of_equity_is_rejected():
    decision = _decision(notional=10_000.0)  # 10% > 5% cap
    result = risk_manager.evaluate(decision, _account())
    assert result.approved_by_risk_manager is False
    assert "exceeds max per-trade" in result.rejection_reason


def test_open_with_no_notional_is_rejected():
    decision = _decision(notional=None)
    result = risk_manager.evaluate(decision, _account())
    assert result.approved_by_risk_manager is False


def test_open_blocked_when_max_positions_reached():
    positions = [SimpleNamespace(symbol="ETHUSD")] * 3
    decision = _decision(notional=1000.0)
    result = risk_manager.evaluate(decision, _account(positions=positions))
    assert result.approved_by_risk_manager is False
    assert "max open positions" in result.rejection_reason


def test_close_with_no_open_position_is_rejected():
    decision = _decision(action=DecisionAction.CLOSE, notional=None)
    result = risk_manager.evaluate(decision, _account(positions=[]))
    assert result.approved_by_risk_manager is False
    assert "no open position" in result.rejection_reason


def test_close_with_open_position_is_approved():
    positions = [SimpleNamespace(symbol="BTCUSD")]
    decision = _decision(action=DecisionAction.CLOSE, notional=None)
    result = risk_manager.evaluate(decision, _account(positions=positions))
    assert result.approved_by_risk_manager is True


def test_close_is_never_blocked_by_cooldown():
    # An OPEN followed immediately by a CLOSE on the same symbol must not be
    # blocked -- a cooldown must never prevent exiting a position.
    account = _account(positions=[SimpleNamespace(symbol="BTCUSD")])
    opened = risk_manager.evaluate(_decision(action=DecisionAction.OPEN, notional=1000.0), account)
    assert opened.approved_by_risk_manager is True

    closed = risk_manager.evaluate(_decision(action=DecisionAction.CLOSE, notional=None), account)
    assert closed.approved_by_risk_manager is True


def test_cooldown_blocks_second_trade_on_same_symbol():
    account = _account()
    first = risk_manager.evaluate(_decision(notional=1000.0), account)
    assert first.approved_by_risk_manager is True

    second = risk_manager.evaluate(_decision(notional=1000.0), account)
    assert second.approved_by_risk_manager is False
    assert "cooldown" in second.rejection_reason


def test_daily_loss_halt_blocks_new_open(monkeypatch):
    monkeypatch.setattr(risk_manager, "get_todays_realized_pnl", lambda: -5000.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 0.03)  # 3% of 100k = 3000
    decision = _decision(notional=1000.0)
    result = risk_manager.evaluate(decision, _account(equity=100_000.0))
    assert result.approved_by_risk_manager is False
    assert "daily loss limit" in result.rejection_reason
