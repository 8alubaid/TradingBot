"""Journal Agent: Claude writes a short markdown 'lessons learned' entry for each decision.

Like the Decision Agent, this has no built-in tools -- it only ever produces text that
gets wrapped with frontmatter and saved via core.storage.save_lesson (local + GCS mirror).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from core.config import settings
from core.models import Signal, TradeDecision, TradeRecord
from core.storage import save_lesson

logger = logging.getLogger("journal_agent")

SYSTEM_PROMPT = """You are the journaling agent for a small crypto paper-trading bot. Given a
signal, the decision made about it, and the resulting trade record, write a short markdown
journal entry capturing what happened and why, so a human (or a future run of the decision
agent) can learn from it later. Be honest about uncertainty. Keep it under 200 words. Do not
include a title heading -- start directly with the body text."""


def _build_prompt(signal: Signal, decision: TradeDecision, record: TradeRecord) -> str:
    return f"""Signal: {signal.action.value} {signal.symbol} @ {signal.price} ({signal.timeframe})
Decision: {decision.action.value} (side={decision.side.value if decision.side else "none"}, \
notional=${decision.notional_usd or 0:.2f})
Decision rationale: {decision.rationale}
Trade record status: {record.status.value}
Order id: {record.order_id or "n/a"}

Write the lessons-learned journal entry now."""


async def write_entry(signal: Signal, decision: TradeDecision, record: TradeRecord) -> None:
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        permission_mode="bypassPermissions",
        max_turns=1,
        env={"ANTHROPIC_API_KEY": settings.anthropic_api_key},
    )

    body = ""
    error_detail: str | None = None
    try:
        async for message in query(prompt=_build_prompt(signal, decision, record), options=options):
            if isinstance(message, AssistantMessage):
                if message.error:
                    error_detail = message.error
                    continue
                for block in message.content:
                    if isinstance(block, TextBlock):
                        body += block.text
            elif isinstance(message, ResultMessage) and message.is_error:
                error_detail = message.result or error_detail or "unknown error"
    except Exception as exc:  # noqa: BLE001 - any SDK/transport failure falls back to a minimal entry
        error_detail = error_detail or str(exc)

    if error_detail:
        logger.error("Journal agent request failed: %s; writing a minimal fallback entry", error_detail)
        body = ""

    if not body.strip():
        body = (
            f"Decision: {decision.action.value} on {signal.symbol}. "
            f"Rationale: {decision.rationale}. Status: {record.status.value}."
        )

    frontmatter = (
        "---\n"
        f"trade_id: {record.id}\n"
        f"date: {datetime.now(timezone.utc).isoformat()}\n"
        f"symbol: {signal.symbol}\n"
        f"decision: {decision.action.value}\n"
        f"status: {record.status.value}\n"
        "---\n\n"
    )

    save_lesson(record.id, frontmatter + body.strip() + "\n")
