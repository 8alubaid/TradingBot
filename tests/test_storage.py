from datetime import datetime, timedelta, timezone

import pytest

import core.storage as storage
from core.config import settings
from core.models import DecisionAction, SignalAction, TradeRecord, TradeStatus


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "trades.db"))
    monkeypatch.setattr(settings, "local_lessons_dir", str(tmp_path / "lessons"))
    monkeypatch.setattr(settings, "gcs_bucket_name", "")


def _record(symbol="BTC/USD", status=TradeStatus.HELD, pnl=None, minutes_ago=0, decision=DecisionAction.HOLD):
    return TradeRecord(
        symbol=symbol,
        signal_action=SignalAction.BUY,
        decision_action=decision,
        status=status,
        pnl_usd=pnl,
        rationale="test",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_get_recent_trades_filters_by_symbol_and_status():
    storage.save_trade(_record(symbol="BTC/USD", status=TradeStatus.HELD))
    storage.save_trade(_record(symbol="ETH/USD", status=TradeStatus.FILLED, decision=DecisionAction.OPEN))

    btc_only = storage.get_recent_trades(symbol="BTC/USD")
    assert len(btc_only) == 1
    assert btc_only[0]["symbol"] == "BTC/USD"

    filled_only = storage.get_recent_trades(status="filled")
    assert len(filled_only) == 1
    assert filled_only[0]["status"] == "filled"


def test_get_health_stats_computes_rates_within_window():
    storage.save_trade(_record(status=TradeStatus.HELD, minutes_ago=5))
    storage.save_trade(_record(status=TradeStatus.HELD, minutes_ago=5))
    storage.save_trade(_record(status=TradeStatus.FILLED, decision=DecisionAction.OPEN, minutes_ago=5))
    storage.save_trade(_record(status=TradeStatus.REJECTED, minutes_ago=5))
    # Outside the 1-hour window -- should not count.
    storage.save_trade(_record(status=TradeStatus.HELD, minutes_ago=120))

    health = storage.get_health_stats(hours=1)

    assert health["total"] == 4
    assert health["held_pct"] == 50.0
    assert health["executed_pct"] == 25.0
    assert health["rejected_pct"] == 25.0


def test_get_health_stats_empty_window():
    health = storage.get_health_stats(hours=1)
    assert health["total"] == 0
    assert health["held_pct"] == 0.0


def test_get_performance_summary():
    storage.save_trade(_record(status=TradeStatus.CLOSED, pnl=100.0, decision=DecisionAction.CLOSE))
    storage.save_trade(_record(status=TradeStatus.CLOSED, pnl=-40.0, decision=DecisionAction.CLOSE))
    storage.save_trade(_record(status=TradeStatus.HELD))

    perf = storage.get_performance_summary()

    assert perf["closed_count"] == 2
    assert perf["total_realized_pnl"] == 60.0
    assert perf["win_rate_pct"] == 50.0
    assert perf["trades_all_time"] == 3


def test_get_performance_summary_no_closed_trades():
    storage.save_trade(_record(status=TradeStatus.HELD))
    perf = storage.get_performance_summary()
    assert perf["closed_count"] == 0
    assert perf["win_rate_pct"] is None


def test_parse_and_strip_frontmatter():
    text = "---\ntrade_id: abc123\nsymbol: BTC/USD\n---\n\nThis is the body."
    meta = storage.parse_frontmatter(text)
    assert meta == {"trade_id": "abc123", "symbol": "BTC/USD"}
    assert storage.strip_frontmatter(text) == "This is the body."


def test_strip_frontmatter_handles_text_without_frontmatter():
    assert storage.strip_frontmatter("just a body") == "just a body"


def test_list_all_lessons(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(exist_ok=True)
    (lessons_dir / "abc123.md").write_text(
        "---\ntrade_id: abc123\ndate: 2026-08-05T10:00:00\nsymbol: BTC/USD\ndecision: open\nstatus: filled\n---\n\n"
        "Opened a starter long position on strong momentum.",
        encoding="utf-8",
    )

    lessons = storage.list_all_lessons()

    assert len(lessons) == 1
    assert lessons[0]["trade_id"] == "abc123"
    assert lessons[0]["symbol"] == "BTC/USD"
    assert "starter long" in lessons[0]["snippet"]
