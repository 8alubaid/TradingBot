"""Orchestrates a Signal through Decision -> Risk -> Execution -> Journal -> Notify -> Storage.

This is deliberately procedural (not a fully autonomous agent handoff): the two
Claude-powered steps (decision, journal) are bracketed by deterministic code, and
the risk manager can only ever downgrade a decision, never place a trade itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alpaca.trading.enums import OrderSide

from agents.decision_agent import decide
from agents.journal_agent import write_entry
from core import execution, risk_manager, storage
from core.events import publish_event
from core.models import DecisionAction, Signal, SignalAction, TradeRecord, TradeStatus
from core.notify import get_notifier

logger = logging.getLogger("pipeline")


async def process_signal(signal: Signal) -> TradeRecord:
    account = execution.get_account_state()

    decision = await decide(signal, account)
    decision = risk_manager.evaluate(decision, account)

    publish_event(
        "decision_made",
        {
            "symbol": signal.symbol,
            "action": decision.action.value,
            "approved_by_risk_manager": decision.approved_by_risk_manager,
            "notional_usd": decision.notional_usd,
        },
    )

    record = TradeRecord(
        symbol=signal.symbol,
        signal_action=signal.action,
        decision_action=decision.action,
        qty=decision.qty,
        notional_usd=decision.notional_usd,
        price=signal.price,
        rationale=decision.rationale,
    )

    if decision.action == DecisionAction.HOLD:
        record.status = TradeStatus.HELD
    elif not decision.approved_by_risk_manager:
        record.status = TradeStatus.REJECTED
        if decision.rejection_reason:
            record.rationale = f"{record.rationale}\n\n[risk manager] {decision.rejection_reason}"
    else:
        try:
            if decision.action == DecisionAction.OPEN:
                side = OrderSide.BUY if decision.side == SignalAction.BUY else OrderSide.SELL
                order = execution.place_market_order(
                    symbol=signal.symbol,
                    side=side,
                    qty=decision.qty,
                    notional=decision.notional_usd,
                )
                record.order_id = str(order.id)
                record.status = TradeStatus.FILLED
            elif decision.action == DecisionAction.CLOSE:
                # Capture unrealized P/L just before closing as an approximation of
                # realized P/L -- exact fill-price accounting isn't implemented (see
                # README known limitations). This is what feeds the risk manager's
                # daily loss kill-switch.
                position = account.position_for(signal.symbol)
                order = execution.close_position(signal.symbol)
                record.order_id = str(order.id)
                record.status = TradeStatus.CLOSED
                record.closed_at = datetime.now(timezone.utc)
                if position is not None:
                    record.pnl_usd = float(position.unrealized_pl)
        except Exception as exc:  # noqa: BLE001 - surface any broker error into the record/notification
            logger.exception("Order placement failed")
            record.status = TradeStatus.ERROR
            record.rationale = f"{record.rationale}\n\n[execution error] {exc}"

    if record.status in (TradeStatus.FILLED, TradeStatus.CLOSED):
        publish_event(
            "trade_executed",
            {
                "trade_id": record.id,
                "symbol": record.symbol,
                "status": record.status.value,
                "order_id": record.order_id,
                "notional_usd": record.notional_usd,
                "pnl_usd": record.pnl_usd,
            },
        )

    storage.save_trade(record)
    await write_entry(signal, decision, record)

    notifier = get_notifier()
    notifier.send(_summary(record))

    return record


def _summary(record: TradeRecord) -> str:
    lines = [
        f"[{record.status.value.upper()}] {record.symbol} — {record.decision_action.value}",
    ]
    if record.qty:
        lines.append(f"qty: {record.qty}")
    if record.notional_usd:
        lines.append(f"notional: ${record.notional_usd:.2f}")
    if record.order_id:
        lines.append(f"order: {record.order_id}")
    lines.append(record.rationale.strip().split("\n")[0][:200])
    return "\n".join(lines)
