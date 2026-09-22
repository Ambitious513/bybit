"""TASK-017 research-engine tests. All network-facing dependencies are mocked."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest


def _modules():
    """Import runtime modules after pytest's environment fixture has run."""
    from bybit_bot import research, screener
    return research, screener


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide configured placeholders before runtime modules are imported."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _flow(ratio=1.2, side="Bullish", funding=0.0001, current=100.0, prior=100.0):
    return {
        "longShortRatio": ratio, "fundSide": side,
        "fundingRate": {"latest": funding}, "topTraderPositionRate": ratio,
        "openInterestHistory": {"current": current, "thirtyDaysAgo": prior},
    }


def _ticker(price="100", change="0.10", high="105", low="95"):
    return {"price": price, "price24hPcnt": change, "turnover24h": "2000000", "high24h": high, "low24h": low}


def test_classify_btc_regime_bullish():
    research, _ = _modules()
    assert research.classify_btc_regime(_flow(1.06), {}, {"value": 50, "classification": "Neutral"})["regime"] == "BULLISH"


def test_classify_btc_regime_bearish():
    research, _ = _modules()
    assert research.classify_btc_regime(_flow(0.94, "Bearish"), {}, {"value": 50, "classification": "Neutral"})["regime"] == "BEARISH"


def test_classify_btc_regime_choppy():
    research, _ = _modules()
    assert research.classify_btc_regime(_flow(1.0), {}, {"value": 50, "classification": "Neutral"})["regime"] == "CHOPPY"


def test_classify_sub_type_strong():
    research, _ = _modules()
    assert research.classify_btc_regime(_flow(1.30), {}, {"value": 50, "classification": "Neutral"})["sub_type"] == "STRONG"


def test_gainers_filter_excludes_skip_list(monkeypatch):
    _, screener = _modules()
    monkeypatch.setattr(screener.bybit_api, "get_all_linear_tickers", lambda: [
        {**_ticker(change="0.12"), "symbol": "ZECUSDT"},
        {**_ticker(change="0.12"), "symbol": "GOODUSDT"},
    ])
    assert [coin["symbol"] for coin in screener.get_gainers()] == ["GOODUSDT"]


def test_gainers_filter_range(monkeypatch):
    _, screener = _modules()
    monkeypatch.setattr(screener.bybit_api, "get_all_linear_tickers", lambda: [
        {**_ticker(change="0.08"), "symbol": "EDGEUSDT"},
        {**_ticker(change="0.09"), "symbol": "KEEPUSDT"},
        {**_ticker(change="0.51"), "symbol": "TOOHIGHUSDT"},
    ])
    assert [coin["symbol"] for coin in screener.get_gainers()] == ["KEEPUSDT"]


def test_losers_only_in_bullish_regime():
    research, _ = _modules()
    loser = {"symbol": "DROPUSDT", "tag": "LOSER", "price24hPcnt": -12}
    bearish = {"regime": "BEARISH"}
    with patch.object(research.data_aggregator, "get_capital_flow", return_value=_flow()), \
         patch.object(research.openrouter, "complete", return_value=None):
        assert research.qualify_coins([], [loser], [], [], bearish) == []


def test_qualify_coins_instant_disqualify():
    research, _ = _modules()
    gainer = {"symbol": "SAFEUSDT", "tag": "GAINER", "price24hPcnt": 12}
    with patch.object(research.data_aggregator, "get_capital_flow", return_value=_flow()), \
         patch.object(research.openrouter, "complete", return_value='{"unlock_today": true}'):
        assert research.qualify_coins([gainer], [], [], [], {"regime": "BULLISH"}) == []


def test_extreme_event_halts_research(monkeypatch, tmp_path):
    research, _ = _modules()
    monkeypatch.setattr(research, "_CACHE_PATH", str(tmp_path / "research_cache.json"))
    monkeypatch.setattr(research, "KNOWN_EVENTS", {datetime.utcnow().date(): ["FOMC decision"]})
    with patch.object(research.bybit_api, "get_ticker", return_value=_ticker()), \
         patch.object(research.data_aggregator, "get_capital_flow", return_value=_flow()), \
         patch.object(research.screener, "get_gainers", return_value=[]), \
         patch.object(research.screener, "get_standing_watchlist", return_value=[]), \
         patch.object(research, "scan_tradfi_perps", return_value=[]), \
         patch.object(research.sr_calculator, "get_sr_levels", return_value={"entry_zone_bottom": 64000.0}), \
         patch.object(research, "_fetch_fear_greed", return_value={"value": 50, "classification": "Neutral"}), \
         patch.object(research.telegram, "send_message"):
        assert research.run_research()["halt_research"] is True


def test_research_cache_written(monkeypatch, tmp_path):
    research, _ = _modules()
    cache_path = tmp_path / "research_cache.json"
    monkeypatch.setattr(research, "_CACHE_PATH", str(cache_path))
    with patch.object(research.bybit_api, "get_ticker", return_value=_ticker()), \
         patch.object(research.data_aggregator, "get_capital_flow", return_value=_flow()), \
         patch.object(research.screener, "get_gainers", return_value=[]), \
         patch.object(research.screener, "get_standing_watchlist", return_value=[]), \
         patch.object(research.screener, "get_losers", return_value=[]), \
         patch.object(research, "scan_tradfi_perps", return_value=[]), \
         patch.object(research.sr_calculator, "get_sr_levels", return_value={"entry_zone_bottom": 64000.0}), \
         patch.object(research, "_fetch_fear_greed", return_value={"value": 50, "classification": "Neutral"}), \
         patch.object(research, "_fetch_news", return_value={"sentiment": "NEUTRAL", "unlock_today": False, "exploit_today": False}), \
         patch.object(research.telegram, "send_message"):
        result = research.run_research()
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["timestamp"] == result["timestamp"]
    assert datetime.fromisoformat(saved["timestamp"])
    assert saved["regime"]["btc_support"] == 64000.0


def test_tradfi_scan_qualifies_high_whale(monkeypatch):
    research, _ = _modules()
    monkeypatch.setattr(research, "TRADFI_PERPS", ["COINUSDT"])
    monkeypatch.setattr(research.data_aggregator, "get_capital_flow", lambda symbol: _flow(2.0))
    monkeypatch.setattr(research.bybit_api, "get_ticker", lambda symbol: _ticker())
    assert research.scan_tradfi_perps()[0]["symbol"] == "COINUSDT"
