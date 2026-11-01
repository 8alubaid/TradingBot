"""Market data enrichment for the dashboard, from two real, documented REST APIs:

- Alpaca's Market Data API: the live price the bot would actually trade at.
- CoinGecko's free public API: 24h change, market cap, volume -- context Alpaca's
  API doesn't provide.

Results are cached briefly server-side since the dashboard polls this on an
interval and CoinGecko's free tier is rate-limited; the cache means N concurrent
dashboard viewers still only cost one upstream call per TTL window.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest

from core.config import settings

logger = logging.getLogger("market_data")

COINGECKO_IDS = {
    "BTC/USD": "bitcoin",
    "ETH/USD": "ethereum",
    "XRP/USD": "ripple",
    "DOGE/USD": "dogecoin",
    "SOL/USD": "solana",
}

_CACHE_TTL_SECONDS = 30
_cache: Optional[list[dict]] = None
_cache_time: float = 0.0


def _fetch_alpaca_prices(symbols: list[str]) -> dict[str, float]:
    try:
        client = CryptoHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)
        trades = client.get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=symbols))
        return {sym: float(t.price) for sym, t in trades.items()}
    except Exception:
        logger.exception("Alpaca market data request failed")
        return {}


def _fetch_coingecko_stats(symbols: list[str]) -> dict[str, dict]:
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    if not ids:
        return {}
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": ",".join(ids), "price_change_percentage": "24h"},
            timeout=10,
        )
        resp.raise_for_status()
        by_id = {row["id"]: row for row in resp.json()}
    except Exception:
        logger.exception("CoinGecko market data request failed")
        return {}

    return {
        symbol: by_id[coingecko_id]
        for symbol, coingecko_id in COINGECKO_IDS.items()
        if symbol in symbols and coingecko_id in by_id
    }


def get_market_overview(symbols: list[str], force_refresh: bool = False) -> list[dict]:
    """One row per symbol: Alpaca's live tradable price + CoinGecko's 24h stats.
    Either source failing independently just leaves those fields None -- a
    CoinGecko outage shouldn't take down the price column and vice versa."""
    global _cache, _cache_time
    now = time.time()
    if not force_refresh and _cache is not None and (now - _cache_time) < _CACHE_TTL_SECONDS:
        return _cache

    alpaca_prices = _fetch_alpaca_prices(symbols)
    coingecko_stats = _fetch_coingecko_stats(symbols)

    rows = []
    for symbol in symbols:
        stats = coingecko_stats.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "price": alpaca_prices.get(symbol),
                "icon": stats["image"] if stats else None,
                "change_24h_pct": stats["price_change_percentage_24h"] if stats else None,
                "market_cap": stats["market_cap"] if stats else None,
                "volume_24h": stats["total_volume"] if stats else None,
            }
        )

    _cache = rows
    _cache_time = now
    return rows
