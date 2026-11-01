"""Shared data models passed between pipeline stages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


class Signal(BaseModel):
    """A parsed TradingView alert."""

    symbol: str
    action: SignalAction
    price: Optional[float] = None
    timeframe: Optional[str] = None
    message: Optional[str] = None
    received_at: datetime = Field(default_factory=_now)
    raw: dict[str, Any] = Field(default_factory=dict)


class DecisionAction(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    HOLD = "hold"


class TradeDecision(BaseModel):
    """Output of the Decision Agent, before risk gating."""

    signal: Signal
    action: DecisionAction
    side: Optional[SignalAction] = None  # buy/sell, meaningful when action == OPEN
    qty: Optional[float] = None
    notional_usd: Optional[float] = None
    rationale: str
    approved_by_risk_manager: bool = False
    rejection_reason: Optional[str] = None


class TradeStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    HELD = "held"
    ERROR = "error"
    CLOSED = "closed"


class TradeRecord(BaseModel):
    """Persisted record of a decision (executed or not)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = Field(default_factory=_now)
    symbol: str
    signal_action: SignalAction
    decision_action: DecisionAction
    qty: Optional[float] = None
    notional_usd: Optional[float] = None
    price: Optional[float] = None
    order_id: Optional[str] = None
    status: TradeStatus = TradeStatus.PENDING
    rationale: str = ""
    pnl_usd: Optional[float] = None
    closed_at: Optional[datetime] = None
