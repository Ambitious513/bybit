"""TASK-021 quick-scan and lightweight regime-flip tests."""

import json
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide configuration placeholders before importing runtime modules."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


@pytest.fixture
def quickscan_paths(tmp_path, monkeypatch):
    """Configure quick-scan local state in a temporary directory."""
    from bybit_bot import quickscan
    monkeypatch.setattr(quickscan, "_RESEARCH_CACHE_PATH", str(tmp_path / "research_cache.json"))
    monkeypatch.setattr(quickscan, "_ACTIVE_ORDERS_PATH", str(tmp_path / "active_orders.json"))
    monkeypatch.setattr(quickscan.telegram, "send_message", lambda message: True)
    return quickscan, tmp_path


def _ticker(symbol, change="0.10", volume="6000000"):
    return {"symbol": symbol, "price": "10", "price24hPcnt": change, "turnover24h": volume}


def _flow(ratio=3.0, side="Bullish"):
    return {"longShortRatio": ratio, "fundSide": side}


def test_should_run_quickscan_false_recent_research(quickscan_paths, monkeypatch):
    quickscan, paths = quickscan_paths
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    monkeypatch.setattr(quickscan, "_utc_now", lambda: now)
    (paths / "research_cache.json").write_text(json.dumps({"timestamp": (now - timedelta(minutes=10)).isoformat()}), encoding="utf-8")
    assert quickscan.should_run_quickscan() is False


def test_should_run_quickscan_false_max_trades(quickscan_paths, monkeypatch):
    quickscan, paths = quickscan_paths
    monkeypatch.setattr(quickscan, "_utc_now", lambda: datetime(2026, 9, 22, 12, tzinfo=UTC))
    (paths / "active_orders.json").write_text(json.dumps([{"status": "FILLED"}] * 3), encoding="utf-8")
    assert quickscan.should_run_quickscan() is False


def test_should_run_quickscan_false_after_hard_close(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    monkeypatch.setattr(quickscan, "_utc_now", lambda: datetime(2026, 9, 22, 20, tzinfo=UTC))
    assert quickscan.should_run_quickscan() is False


def test_should_run_quickscan_true(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    monkeypatch.setattr(quickscan, "_utc_now", lambda: datetime(2026, 9, 22, 12, tzinfo=UTC))
    assert quickscan.should_run_quickscan() is True


def test_quickscan_filters_by_volume(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    monkeypatch.setattr(quickscan, "should_run_quickscan", lambda: True)
    monkeypatch.setattr(quickscan.bybit_api, "get_all_linear_tickers", lambda: [_ticker("LOWUSDT", volume="4999999")])
    assert quickscan.run_quickscan() == []


def test_quickscan_filters_by_price_change(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    monkeypatch.setattr(quickscan, "should_run_quickscan", lambda: True)
    monkeypatch.setattr(quickscan.bybit_api, "get_all_linear_tickers", lambda: [_ticker("LOWUSDT", "0.08"), _ticker("HIGHUSDT", "0.41")])
    assert quickscan.run_quickscan() == []


def test_quickscan_high_conviction_fires_alert(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    sent: list[str] = []
    monkeypatch.setattr(quickscan, "should_run_quickscan", lambda: True)
    monkeypatch.setattr(quickscan.bybit_api, "get_all_linear_tickers", lambda: [_ticker("GOODUSDT")])
    monkeypatch.setattr(quickscan.data_aggregator, "get_capital_flow", lambda symbol: _flow(3.0))
    monkeypatch.setattr(quickscan.telegram, "send_message", sent.append)
    assert quickscan.run_quickscan()[0]["symbol"] == "GOODUSDT"
    assert "HIGH CONVICTION" in sent[0]


def test_quickscan_low_whale_no_alert(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    sent: list[str] = []
    monkeypatch.setattr(quickscan, "should_run_quickscan", lambda: True)
    monkeypatch.setattr(quickscan.bybit_api, "get_all_linear_tickers", lambda: [_ticker("WEAKUSDT")])
    monkeypatch.setattr(quickscan.data_aggregator, "get_capital_flow", lambda symbol: _flow(2.0))
    monkeypatch.setattr(quickscan.telegram, "send_message", sent.append)
    assert quickscan.run_quickscan() == []
    assert sent == []


def test_quickscan_max_5_flow_calls(quickscan_paths, monkeypatch):
    quickscan, _ = quickscan_paths
    calls: list[str] = []
    monkeypatch.setattr(quickscan, "should_run_quickscan", lambda: True)
    monkeypatch.setattr(quickscan.bybit_api, "get_all_linear_tickers", lambda: [_ticker(f"COIN{index}USDT", change=str(.09 + index / 1000)) for index in range(8)])
    monkeypatch.setattr(quickscan.data_aggregator, "get_capital_flow", lambda symbol: calls.append(symbol) or _flow(2.0))
    quickscan.run_quickscan()
    assert len(calls) == 5


def test_regime_flip_detects_bullish_to_bearish(monkeypatch):
    from bybit_bot import orchestrator, quickscan
    sent: list[str] = []
    monkeypatch.setattr(quickscan, "classify_btc_regime_quick", lambda: "BEARISH")
    monkeypatch.setattr(orchestrator, "load_btc_regime_from_cache", lambda: "BULLISH")
    monkeypatch.setattr(orchestrator, "load_active_orders", lambda: [{"symbol": "TAOUSDT", "status": "PENDING"}])
    monkeypatch.setattr(orchestrator, "update_regime_in_cache", lambda regime: None)
    monkeypatch.setattr(orchestrator.telegram, "send_message", sent.append)
    orchestrator._btc_check()
    assert "BTC REGIME FLIP → BEARISH" in sent[0]
    assert "TAOUSDT" in sent[0]


def test_regime_flip_no_alert_if_no_pending(monkeypatch):
    from bybit_bot import orchestrator, quickscan
    sent: list[str] = []
    monkeypatch.setattr(quickscan, "classify_btc_regime_quick", lambda: "BEARISH")
    monkeypatch.setattr(orchestrator, "load_btc_regime_from_cache", lambda: "BULLISH")
    monkeypatch.setattr(orchestrator, "load_active_orders", lambda: [{"symbol": "TAOUSDT", "status": "FILLED"}])
    monkeypatch.setattr(orchestrator, "update_regime_in_cache", lambda regime: None)
    monkeypatch.setattr(orchestrator.telegram, "send_message", sent.append)
    orchestrator._btc_check()
    assert sent == []
