import pytest

from core.models import Signal, SignalAction


def test_signal_parses_minimal_payload():
    payload = {"symbol": "BTC/USD", "action": "buy", "price": 65000.5}
    signal = Signal(
        symbol=payload["symbol"],
        action=SignalAction(payload["action"]),
        price=payload.get("price"),
    )
    assert signal.symbol == "BTC/USD"
    assert signal.action == SignalAction.BUY
    assert signal.price == 65000.5


def test_signal_rejects_unknown_action():
    with pytest.raises(ValueError):
        SignalAction("moon")


def test_signal_action_is_case_normalized_by_caller():
    # server.py lower()s the raw action before constructing SignalAction
    assert SignalAction("sell".lower()) == SignalAction.SELL
