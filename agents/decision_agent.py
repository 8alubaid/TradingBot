"""Decision Agent: Claude reasons about a TradingView signal and outputs a structured decision.

Kept deliberately simple and debuggable: account state and recent lessons are fetched
in Python and embedded directly in the prompt (no live tool-call round trip), and the
model has none of Claude Code's built-in tools (no Bash/Read/Write) -- it can only ever
return a JSON object matching DECISION_SCHEMA. The Risk Manager, not this agent, has
final say over whether a trade actually happens.
"""

from __future__ import annotations

import json
import logging

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from core.config import settings
from core.execution import AccountState
from core.models import DecisionAction, Signal, SignalAction, TradeDecision
from core.storage import recent_lessons_summary

logger = logging.getLogger("decision_agent")

SYSTEM_PROMPT = """You are the risk-aware decision agent for a small crypto paper-trading bot.
You receive one TradingView signal at a time plus the current Alpaca account state and a
summary of recent trades and lessons learned. Decide whether to OPEN a new position, CLOSE
an existing one, or HOLD (do nothing).

Rules of thumb:
- Never risk more than a small fraction of equity on a single trade.
- Prefer HOLD when the signal is weak, contradicts recent lessons, or account risk is already
  elevated (many open positions, recent losses).
- Only propose CLOSE for a symbol you currently hold a position in.
- Be concise but specific in your rationale -- it is stored as a permanent training record.

You have no tools. Respond only with the structured JSON decision."""

DECISION_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["open", "close", "hold"]},
            "side": {"type": "string", "enum": ["buy", "sell", "none"]},
            "notional_usd": {"type": "number"},
            "rationale": {"type": "string"},
        },
        "required": ["action", "side", "notional_usd", "rationale"],
    },
}


def _build_prompt(signal: Signal, account: AccountState) -> str:
    position = account.position_for(signal.symbol)
    position_desc = (
        f"Existing position: {position.qty} {signal.symbol} @ avg entry {position.avg_entry_price}, "
        f"unrealized P/L {position.unrealized_pl}"
        if position
        else "No existing position in this symbol."
    )
    lessons = recent_lessons_summary(limit=5)

    return f"""Signal received:
  symbol: {signal.symbol}
  action: {signal.action.value}
  price: {signal.price}
  timeframe: {signal.timeframe}
  message: {signal.message}

Account state:
  equity: ${account.equity:.2f}
  cash: ${account.cash:.2f}
  buying_power: ${account.buying_power:.2f}
  open_positions: {account.open_position_count()}
  {position_desc}

Recent lessons learned (most recent first):
{lessons or "(none yet)"}

Decide: open, close, or hold. If open, choose side (buy/sell) and a notional_usd amount
that is a small, prudent fraction of equity. If close or hold, set notional_usd to 0 and
side to "none"."""


async def decide(signal: Signal, account: AccountState) -> TradeDecision:
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        output_format=DECISION_SCHEMA,
        permission_mode="bypassPermissions",
        max_turns=1,
        env={"ANTHROPIC_API_KEY": settings.anthropic_api_key},
    )

    result_payload: dict | None = None
    error_detail: str | None = None
    try:
        async for message in query(prompt=_build_prompt(signal, account), options=options):
            if isinstance(message, ResultMessage):
                if message.is_error:
                    error_detail = message.result or "unknown error"
                elif message.structured_output is not None:
                    result_payload = message.structured_output
                elif message.result:
                    try:
                        result_payload = json.loads(message.result)
                    except json.JSONDecodeError:
                        error_detail = message.result
    except Exception as exc:  # noqa: BLE001 - any SDK/transport failure should fail safe to HOLD
        error_detail = error_detail or str(exc)

    if error_detail:
        logger.error("Decision agent request failed: %s", error_detail)
    if result_payload is None:
        reason = f" ({error_detail})" if error_detail else ""
        return TradeDecision(
            signal=signal,
            action=DecisionAction.HOLD,
            rationale=f"No usable response from decision agent{reason}; defaulting to HOLD for safety.",
        )

    action = DecisionAction(result_payload["action"])
    side_raw = result_payload.get("side", "none")
    side = SignalAction(side_raw) if side_raw in ("buy", "sell") else None
    notional = float(result_payload.get("notional_usd") or 0)

    return TradeDecision(
        signal=signal,
        action=action,
        side=side,
        notional_usd=notional if notional > 0 else None,
        rationale=result_payload.get("rationale", ""),
    )
