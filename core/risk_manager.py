"""Deterministic risk guardrails -- plain Python, no LLM call.

Can only ever downgrade a TradeDecision to unapproved; never places a trade and
never overrides a HOLD into something else. This is the hard safety layer that
sits between the Decision Agent's reasoning and any money-moving action.
"""

from __future__ import annotations

import time

from core.config import settings
from core.execution import AccountState
from core.models import DecisionAction, TradeDecision
from core.storage import get_todays_realized_pnl

_last_trade_time: dict[str, float] = {}


def evaluate(decision: TradeDecision, account: AccountState) -> TradeDecision:
    if decision.action == DecisionAction.HOLD:
        decision.approved_by_risk_manager = True
        return decision

    symbol = decision.signal.symbol

    if symbol not in settings.symbol_list:
        return _reject(decision, f"{symbol} is not in the symbol allow-list {settings.symbol_list}")

    if decision.action == DecisionAction.OPEN:
        if account.open_position_count() >= settings.max_open_positions:
            return _reject(decision, f"max open positions ({settings.max_open_positions}) reached")

        if not decision.notional_usd or decision.notional_usd <= 0:
            return _reject(decision, "no positive notional_usd proposed for an OPEN decision")

        max_notional = account.equity * settings.max_position_pct_of_equity
        if decision.notional_usd > max_notional:
            return _reject(
                decision,
                f"notional ${decision.notional_usd:.2f} exceeds max per-trade "
                f"${max_notional:.2f} ({settings.max_position_pct_of_equity:.0%} of equity)",
            )

        if decision.notional_usd > account.buying_power:
            return _reject(decision, "notional exceeds available buying power")

    if decision.action == DecisionAction.CLOSE and not account.position_for(symbol):
        return _reject(decision, f"no open position in {symbol} to close")

    now = time.time()
    if decision.action == DecisionAction.OPEN:
        # Cooldown only gates new entries -- exits must never be blocked by a timer,
        # or the bot could be stuck holding a losing position it's trying to close.
        last = _last_trade_time.get(symbol)
        if last is not None and (now - last) < settings.symbol_cooldown_seconds:
            return _reject(decision, f"{symbol} is in a {settings.symbol_cooldown_seconds}s cooldown")

        if _daily_loss_halted(account):
            return _reject(decision, "daily loss limit reached; new entries halted for today")

    decision.approved_by_risk_manager = True
    _last_trade_time[symbol] = now
    return decision


def _reject(decision: TradeDecision, reason: str) -> TradeDecision:
    decision.approved_by_risk_manager = False
    decision.rejection_reason = reason
    return decision


def _daily_loss_halted(account: AccountState) -> bool:
    realized_today = get_todays_realized_pnl()
    limit = -abs(settings.daily_loss_limit_pct) * account.equity
    return realized_today <= limit
