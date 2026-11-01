import core.market_data as market_data


def _reset_cache():
    market_data._cache = None
    market_data._cache_time = 0.0


def test_all_configured_symbols_have_a_coingecko_id():
    for symbol in ["BTC/USD", "ETH/USD", "XRP/USD", "DOGE/USD", "SOL/USD"]:
        assert symbol in market_data.COINGECKO_IDS


def test_overview_degrades_gracefully_when_both_sources_fail(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(market_data, "_fetch_alpaca_prices", lambda symbols: {})
    monkeypatch.setattr(market_data, "_fetch_coingecko_stats", lambda symbols: {})

    rows = market_data.get_market_overview(["BTC/USD", "ETH/USD"], force_refresh=True)

    assert len(rows) == 2
    for row in rows:
        assert row["price"] is None
        assert row["change_24h_pct"] is None
        assert row["market_cap"] is None


def test_overview_uses_whichever_source_succeeds(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(market_data, "_fetch_alpaca_prices", lambda symbols: {"BTC/USD": 65000.0})
    monkeypatch.setattr(market_data, "_fetch_coingecko_stats", lambda symbols: {})

    rows = market_data.get_market_overview(["BTC/USD"], force_refresh=True)

    assert rows[0]["price"] == 65000.0
    assert rows[0]["market_cap"] is None


def test_overview_cache_avoids_refetching_within_ttl(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_fetch(symbols):
        calls["n"] += 1
        return {"BTC/USD": 65000.0}

    monkeypatch.setattr(market_data, "_fetch_alpaca_prices", fake_fetch)
    monkeypatch.setattr(market_data, "_fetch_coingecko_stats", lambda symbols: {})

    market_data.get_market_overview(["BTC/USD"])
    market_data.get_market_overview(["BTC/USD"])

    assert calls["n"] == 1
