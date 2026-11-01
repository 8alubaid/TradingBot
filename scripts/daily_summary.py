"""Sends a once-daily P&L / activity digest via the configured notifier.

Run manually (`python -m scripts.daily_summary`) or on a schedule -- see
deploy/tradingbot-daily-summary.timer for the systemd timer that runs this
once a day on the GCE VM.
"""

from __future__ import annotations

from core.execution import get_account_state
from core.notify import get_notifier
from core.storage import get_recent_trades, get_todays_realized_pnl


def build_summary() -> str:
    account = get_account_state()
    realized_today = get_todays_realized_pnl()
    recent = get_recent_trades(limit=10)

    lines = [
        "Daily trading bot summary",
        f"Equity: ${account.equity:.2f} | Cash: ${account.cash:.2f}",
        f"Open positions: {account.open_position_count()}",
        f"Realized P&L today: ${realized_today:.2f}",
        "",
        "Last 10 decisions:",
    ]
    for t in recent:
        lines.append(f"  [{t['status']}] {t['symbol']} {t['decision_action']} ({t['timestamp']})")

    return "\n".join(lines)


def main() -> None:
    summary = build_summary()
    get_notifier().send(summary)
    print(summary)


if __name__ == "__main__":
    main()
