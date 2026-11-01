"""Thin wrapper around alpaca-py for crypto account state and order placement.

Crypto orders only support GTC/IOC time-in-force (not DAY) since crypto trades
24/7 -- see https://docs.alpaca.markets/docs/crypto-orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, OrderSide, TimeInForce
from alpaca.trading.requests import GetAssetsRequest, GetOrdersRequest, MarketOrderRequest
from alpaca.trading.models import Order, Position, TradeAccount

from core.config import settings


@lru_cache(maxsize=1)
def get_client() -> TradingClient:
    return TradingClient(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        paper=settings.alpaca_paper,
    )


@dataclass
class AccountState:
    equity: float
    cash: float
    buying_power: float
    positions: list[Position]

    def open_position_count(self) -> int:
        return len(self.positions)

    def position_for(self, symbol: str) -> Optional[Position]:
        for p in self.positions:
            if p.symbol.replace("/", "") == symbol.replace("/", ""):
                return p
        return None


def get_account_state() -> AccountState:
    client = get_client()
    account: TradeAccount = client.get_account()
    positions: list[Position] = client.get_all_positions()
    return AccountState(
        equity=float(account.equity),
        cash=float(account.cash),
        buying_power=float(account.buying_power),
        positions=positions,
    )


def place_market_order(
    symbol: str,
    side: OrderSide,
    qty: Optional[float] = None,
    notional: Optional[float] = None,
) -> Order:
    """Place a crypto market order. Provide exactly one of qty / notional."""
    if (qty is None) == (notional is None):
        raise ValueError("Provide exactly one of qty or notional")

    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        notional=notional,
        side=side,
        time_in_force=TimeInForce.GTC,
    )
    return get_client().submit_order(order_data=order_data)


def close_position(symbol: str) -> Order:
    # The /positions/{symbol} endpoint takes the bare symbol (e.g. "BTCUSD") --
    # unlike order submission, a "/" in the path (e.g. "BTC/USD") 404s.
    return get_client().close_position(symbol_or_asset_id=symbol.replace("/", ""))


def get_recent_orders(limit: int = 20) -> list[Order]:
    req = GetOrdersRequest(limit=limit)
    return get_client().get_orders(filter=req)


@lru_cache(maxsize=1)
def get_tradable_crypto_symbols() -> frozenset[str]:
    """Live set of crypto symbols (e.g. "ETH/USD") actually tradable on this Alpaca
    account. Not every coin people ask for is listed on Alpaca (e.g. BNB, TRX aren't,
    as of this writing) -- check against this rather than assuming a symbol works."""
    assets = get_client().get_all_assets(filter=GetAssetsRequest(asset_class=AssetClass.CRYPTO))
    return frozenset(a.symbol for a in assets if a.tradable)
