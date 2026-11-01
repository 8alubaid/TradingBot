"""Read-only trade dashboard, served from the same FastAPI app as the webhook.

Reuses the existing SQLite trade history and lessons/ markdown journal -- no new
services, no new data store. Renders server-side with Jinja2 to keep the frontend
footprint minimal. Auto-refresh works by re-fetching small HTML partials (the
stats bar, the trades table) on an interval and swapping them in -- same template
used for the initial render and the partial, so there's one source of truth for
the markup instead of duplicating it in JS.
"""

from __future__ import annotations

import logging
from pathlib import Path

import markdown as markdown_lib
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.backtest import MAX_DAYS, run_backtest
from core.config import settings
from core.execution import get_account_state
from core.market_data import get_market_overview
from core.models import TradeStatus
from core.storage import (
    get_health_stats,
    get_lesson_markdown,
    get_performance_summary,
    get_recent_trades,
    get_trade,
    list_all_lessons,
    strip_frontmatter,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = logging.getLogger("dashboard")

# TradingView's public chart widget wants an exchange:ticker symbol, not our
# "BTC/USD" broker format -- map the coins this bot actually trades.
_CHART_SYMBOLS = {
    "BTC/USD": "COINBASE:BTCUSD",
    "ETH/USD": "COINBASE:ETHUSD",
    "XRP/USD": "COINBASE:XRPUSD",
    "DOGE/USD": "COINBASE:DOGEUSD",
    "SOL/USD": "COINBASE:SOLUSD",
}
_DEFAULT_CHART_SYMBOL = "COINBASE:BTCUSD"
_STATUS_CHOICES = [s.value for s in TradeStatus]


def _symbol_choices() -> list[dict]:
    """Picker options limited to symbols the bot is actually configured to trade
    (settings.symbol_list), not the full universe Alpaca happens to list."""
    return [{"label": s, "value": _CHART_SYMBOLS[s]} for s in settings.symbol_list if s in _CHART_SYMBOLS]


def _account_context() -> dict | None:
    try:
        account = get_account_state()
    except Exception:
        return None
    return {
        "equity": account.equity,
        "cash": account.cash,
        "open_positions": account.open_position_count(),
    }


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    return templates.TemplateResponse(
        request,
        "_stats.html",
        {"paper_trading": settings.alpaca_paper, "account": _account_context()},
    )


@router.get("/partials/trades-table", response_class=HTMLResponse)
async def trades_table_partial(
    request: Request,
    symbol: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    trades = get_recent_trades(limit=50, symbol=symbol or None, status=status or None, date_from=date_from or None, date_to=date_to or None)
    return templates.TemplateResponse(request, "_trades_table.html", {"trades": trades})


@router.get("/partials/market-overview", response_class=HTMLResponse)
async def market_overview_partial(request: Request):
    return templates.TemplateResponse(
        request, "_market_overview.html", {"rows": get_market_overview(settings.symbol_list)}
    )


@router.get("/partials/health", response_class=HTMLResponse)
async def health_partial(request: Request):
    return templates.TemplateResponse(request, "_health.html", {"health": get_health_stats(hours=24)})


@router.get("/partials/performance", response_class=HTMLResponse)
async def performance_partial(request: Request):
    return templates.TemplateResponse(request, "_performance.html", {"perf": get_performance_summary()})


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, symbol: str | None = None):
    trades = get_recent_trades(limit=50)
    choices = _symbol_choices()

    chart_symbol = _CHART_SYMBOLS.get(symbol) if symbol else None
    if not chart_symbol:
        chart_symbol = _CHART_SYMBOLS.get(trades[0]["symbol"]) if trades else None
    if not chart_symbol:
        chart_symbol = choices[0]["value"] if choices else _DEFAULT_CHART_SYMBOL

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "trades": trades,
            "chart_symbol": chart_symbol,
            "symbol_choices": choices,
            "filter_symbols": settings.symbol_list,
            "filter_statuses": _STATUS_CHOICES,
            "rows": get_market_overview(settings.symbol_list),
            "health": get_health_stats(hours=24),
            "perf": get_performance_summary(),
            "paper_trading": settings.alpaca_paper,
            "account": _account_context(),
        },
    )


@router.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request):
    return templates.TemplateResponse(
        request,
        "backtest.html",
        {
            "filter_symbols": settings.symbol_list,
            "max_days": MAX_DAYS,
            "paper_trading": settings.alpaca_paper,
            "account": _account_context(),
        },
    )


@router.get("/backtest/run", response_class=HTMLResponse)
async def backtest_run(
    request: Request,
    symbol: str,
    bar_minutes: int = 15,
    days: int = 7,
    notional_usd: float = 200.0,
):
    if symbol not in settings.symbol_list:
        raise HTTPException(status_code=400, detail="symbol not in SYMBOL_ALLOWLIST")

    result = None
    error = None
    try:
        result = run_backtest(symbol=symbol, bar_minutes=bar_minutes, days=days, notional_usd=notional_usd)
    except Exception as exc:
        logger.exception("Backtest failed for %s (%smin bars, %s days)", symbol, bar_minutes, days)
        error = str(exc)

    return templates.TemplateResponse(request, "_backtest_results.html", {"result": result, "error": error})


@router.get("/lessons", response_class=HTMLResponse)
async def lessons_archive(request: Request):
    return templates.TemplateResponse(
        request,
        "lessons.html",
        {
            "lessons": list_all_lessons(),
            "paper_trading": settings.alpaca_paper,
            "account": _account_context(),
        },
    )


@router.get("/trade/{trade_id}", response_class=HTMLResponse)
async def trade_detail(request: Request, trade_id: str):
    trade = get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")

    lesson_md = get_lesson_markdown(trade_id)
    lesson_html = markdown_lib.markdown(strip_frontmatter(lesson_md)) if lesson_md else None

    return templates.TemplateResponse(
        request,
        "trade_detail.html",
        {
            "trade": trade,
            "lesson_html": lesson_html,
            "paper_trading": settings.alpaca_paper,
            "account": _account_context(),
        },
    )
