"""TASK-025 tests for command correctness and paper-account observability."""

import json
from datetime import UTC, datetime

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide configuration environment expected by imported bot modules."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _patch_deepdive_dependencies(monkeypatch):
    """Stub external deep-dive dependencies and capture its generated setup."""
    from bybit_bot import data_aggregator, execution, openrouter, orchestrator, planning, sr_calculator

    captured: list[dict] = []
    monkeypatch.setattr(orchestrator.bybit_api, "get_ticker", lambda symbol: {"price": "100"})
    monkeypatch.setattr(data_aggregator, "get_capital_flow", lambda symbol: {"longShortRatio": 2.0, "fundSide": "Bullish"})
    monkeypatch.setattr(sr_calculator, "get_sr_levels", lambda symbol, price: {"sl_level": 95.0})
    monkeypatch.setattr(openrouter, "complete", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(openrouter, "parse_json_response", lambda response: {})
    monkeypatch.setattr(planning, "score_coin", lambda *args: 50)
    monkeypatch.setattr(execution, "generate_card", lambda setup, risk_override=None: captured.append(setup) or {"symbol": setup["symbol"]})
    monkeypatch.setattr(orchestrator.telegram, "send_message", lambda message: True)
    return orchestrator, captured


def _patch_market_dependencies(monkeypatch):
    """Stub external market-card dependencies and capture setup plus risk override."""
    from bybit_bot import data_aggregator, execution, orchestrator, sr_calculator

    captured: list[tuple[dict, float | None]] = []
    monkeypatch.setattr(orchestrator.bybit_api, "get_ticker", lambda symbol: {"price": "100"})
    monkeypatch.setattr(data_aggregator, "get_capital_flow", lambda symbol: {"longShortRatio": 2.0, "fundSide": "Bullish"})
    monkeypatch.setattr(sr_calculator, "get_sr_levels", lambda symbol, price: {"sl_level": 95.0})
    monkeypatch.setattr(
        execution,
        "generate_card",
        lambda setup, risk_override=None: captured.append((setup, risk_override)) or {"symbol": setup["symbol"]},
    )
    monkeypatch.setattr(orchestrator.telegram, "send_message", lambda message: True)
    return orchestrator, execution, captured


def test_deepdive_tradfi_symbol_gets_tradfi_tag(monkeypatch):
    """A configured TradFi perpetual is passed to execution with its correct tag."""
    orchestrator, captured = _patch_deepdive_dependencies(monkeypatch)
    orchestrator.handle_deepdive("XAU")
    assert captured[0]["tag"] == "TRADFI"


def test_deepdive_crypto_symbol_gets_crypto_tag(monkeypatch):
    """A non-TradFi perpetual keeps the CRYPTO tag in deep-dive execution."""
    orchestrator, captured = _patch_deepdive_dependencies(monkeypatch)
    orchestrator.handle_deepdive("WIF")
    assert captured[0]["tag"] == "CRYPTO"


def test_market_tradfi_symbol_gets_tradfi_tag(monkeypatch):
    """Weekend market cards use the source-of-truth TradFi instrument tag."""
    orchestrator, _, captured = _patch_market_dependencies(monkeypatch)
    orchestrator.handle_market("NVDA")
    assert captured[0][0]["tag"] == "TRADFI"


def test_market_risk_override_uses_approved_value(monkeypatch):
    """Market cards pass the immutable approved market-risk value explicitly."""
    from bybit_bot.config import PAPER_RISK_MARKET

    orchestrator, _, captured = _patch_market_dependencies(monkeypatch)
    orchestrator.handle_market("WIF")
    assert captured[0][1] == PAPER_RISK_MARKET


def test_generate_card_rejects_invalid_risk_override(tmp_path, monkeypatch):
    """Card generation rejects a caller-provided risk outside the approved values."""
    from bybit_bot import execution

    cache_path = tmp_path / "research_cache.json"
    cache_path.write_text(json.dumps({"regime": {"direction": "LONG", "regime": "BULLISH"}}), encoding="utf-8")
    monkeypatch.setattr(execution, "_RESEARCH_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(execution, "_ACTIVE_ORDERS_PATH", str(tmp_path / "active_orders.json"))

    class FrozenDateTime(datetime):
        """Keep this validation test independent of the UTC hard-close hour."""

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 22, 12, tzinfo=UTC)

    monkeypatch.setattr(execution, "datetime", FrozenDateTime)
    setup = {
        "symbol": "GOODUSDT", "current_price": 100.0, "tag": "CRYPTO", "flow": {},
        "sr": {"entry_mid": 98.0, "entry_zone_top": 97.0, "entry_zone_bottom": 96.0, "sl_level": 95.0, "stop_dist_pct": 3.0},
    }
    assert execution.generate_card(setup, risk_override=5.0) is None


def test_generate_card_no_global_mutation(monkeypatch):
    """Market-card generation no longer changes execution's normal-risk global."""
    from bybit_bot.config import PAPER_RISK_PER_TRADE

    orchestrator, execution, _ = _patch_market_dependencies(monkeypatch)
    orchestrator.handle_market("WIF")
    assert execution.PAPER_RISK_PER_TRADE == PAPER_RISK_PER_TRADE


def test_log_trade_includes_tp_fields(tmp_path, monkeypatch):
    """New closed-trade records include the target and sizing inputs for balance estimates."""
    from bybit_bot import monitor

    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor.telegram, "send_message", lambda message: True)
    monitor.log_trade({
        "symbol": "GOODUSDT", "side": "LONG", "entry": 100.0, "sl": 95.0,
        "tp1": 105.0, "tp3": 115.0, "notional": 100.0, "leverage": 5,
        "stop_dist_pct": 5.0, "paper_risk": 2.0,
    }, 115.0, "TP3")
    record = json.loads((tmp_path / "trade_log.json").read_text(encoding="utf-8"))[0]
    assert {"tp1", "tp3", "notional"}.issubset(record)


def test_log_trade_includes_fill_price(tmp_path, monkeypatch):
    """New closed-trade records retain actual-fill information for accurate P&L review."""
    from bybit_bot import monitor

    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor.telegram, "send_message", lambda message: True)
    monitor.log_trade({
        "symbol": "GOODUSDT", "side": "LONG", "entry": 100.0, "planned_entry": 100.0,
        "fill_price": 101.0, "notional": 100.0, "paper_risk": 2.0,
    }, 110.0, "MANUAL_CLOSE")
    record = json.loads((tmp_path / "trade_log.json").read_text(encoding="utf-8"))[0]
    assert record["fill_price"] == 101.0


def test_balance_breakdown_label_includes_estimated(monkeypatch):
    """Optional core/runner display makes its projection status explicit to operators."""
    from bybit_bot import orchestrator

    messages: list[str] = []
    monkeypatch.setattr(orchestrator, "_load_balance", lambda: {"balance": 20.0, "total_trades": 1, "wins": 1, "losses": 0, "total_pnl": 1.0})
    monkeypatch.setattr(orchestrator, "_load_trade_log", lambda: [{"entry": 100.0, "tp1": 102.0, "tp3": 105.0, "notional": 100.0, "side": "LONG"}])
    monkeypatch.setattr(orchestrator.telegram, "send_message", messages.append)
    orchestrator.handle_balance()
    assert "estimated" in messages[0]


def test_btc_support_below_current_btc_price(monkeypatch, tmp_path):
    """The stored BTC key-zone value remains below the current BTC price."""
    from bybit_bot import data_aggregator, research

    monkeypatch.setattr(research, "_CACHE_PATH", str(tmp_path / "research_cache.json"))
    monkeypatch.setattr(research.bybit_api, "get_ticker", lambda symbol: {
        "price": "65000", "high24h": "66000", "low24h": "64000", "price24hPcnt": "0.01",
    })
    monkeypatch.setattr(data_aggregator, "get_capital_flow", lambda symbol: {"fundingRate": "0"})
    monkeypatch.setattr(research, "_fetch_fear_greed", lambda: {"value": 50, "classification": "Neutral"})
    monkeypatch.setattr(research.screener, "get_gainers", lambda: [])
    monkeypatch.setattr(research.screener, "get_standing_watchlist", lambda: [])
    monkeypatch.setattr(research, "scan_tradfi_perps", lambda: [])
    monkeypatch.setattr(research, "classify_btc_regime", lambda *args: {
        "regime": "CHOPPY", "sub_type": "NEUTRAL", "direction": "SHORT", "btc_price": 65000.0,
        "whale_ratio": 1.0, "fund_side": "NEUTRAL", "fear_greed": {"value": 50, "classification": "Neutral"},
        "warnings": [],
    })
    monkeypatch.setattr(research.sr_calculator, "get_sr_levels", lambda *args: {"entry_zone_bottom": 64000.0})
    monkeypatch.setattr(research, "_events_today", lambda: [])
    monkeypatch.setattr(research, "_fetch_news", lambda *args: {})
    monkeypatch.setattr(research, "qualify_coins", lambda *args: [])
    monkeypatch.setattr(research.telegram, "send_message", lambda message: True)

    result = research.run_research(cache_only=True)
    assert 0.0 < result["regime"]["btc_support"] < 65000.0
