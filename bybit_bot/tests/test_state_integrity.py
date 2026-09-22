"""TASK-023 tests for active-order atomicity and execution safety guards."""

import json
import threading
from datetime import UTC, datetime

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide the configuration environment expected by imported modules."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _setup(symbol: str = "GOODUSDT", stop: float = 3.0) -> dict:
    """Build a valid deterministic setup for execution-card tests."""
    entry = 98.0
    return {
        "symbol": symbol,
        "tag": "GAINER",
        "current_price": 100.0,
        "flow": {"longShortRatio": 2.0, "fundSide": "Bullish"},
        "news": {},
        "score": 50,
        "sr": {
            "entry_mid": entry,
            "entry_zone_top": 97.0,
            "entry_zone_bottom": 96.0,
            "sl_level": entry * (1 - stop / 100),
            "stop_dist_pct": stop,
        },
    }


@pytest.fixture
def state_paths(tmp_path, monkeypatch):
    """Direct execution, monitor, and command state to one isolated directory."""
    from bybit_bot import execution, monitor, orchestrator

    order_path = tmp_path / "active_orders.json"
    cache_path = tmp_path / "research_cache.json"
    cache_path.write_text(
        json.dumps({"regime": {"direction": "LONG", "regime": "BULLISH", "btc_price": 65000.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(execution, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(execution, "_ACTIVE_ORDERS_PATH", str(order_path))
    monkeypatch.setattr(execution, "_RESEARCH_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(monitor, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "_ACTIVE_ORDERS_PATH", str(order_path))
    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor, "_PAPER_BALANCE_PATH", str(tmp_path / "paper_balance.json"))
    monkeypatch.setattr(monitor, "_RESEARCH_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(orchestrator, "_bot_data_path", lambda filename: str(tmp_path / filename))
    monkeypatch.setattr(execution.telegram, "send_card", lambda lines: True)
    monkeypatch.setattr(monitor.telegram, "send_message", lambda message: True)
    monkeypatch.setattr(orchestrator.telegram, "send_message", lambda message: True)
    return execution, monitor, orchestrator, order_path


def test_session_cap_blocks_fourth_card(state_paths):
    """A fourth active paper order must not receive an execution card."""
    execution, _, _, order_path = state_paths
    order_path.write_text(
        json.dumps([{"symbol": f"COIN{index}USDT", "status": "PENDING"} for index in range(3)]),
        encoding="utf-8",
    )
    assert execution.generate_card(_setup("FOURTHUSDT")) is None


def test_deduplication_blocks_pending_symbol(state_paths):
    """A PENDING symbol must not receive a duplicate execution card."""
    execution, _, _, order_path = state_paths
    order_path.write_text(json.dumps([{"symbol": "GOODUSDT", "status": "PENDING"}]), encoding="utf-8")
    assert execution.generate_card(_setup()) is None


def test_deduplication_blocks_filled_symbol(state_paths):
    """A FILLED symbol must not receive a duplicate execution card."""
    execution, _, _, order_path = state_paths
    order_path.write_text(json.dumps([{"symbol": "GOODUSDT", "status": "FILLED"}]), encoding="utf-8")
    assert execution.generate_card(_setup()) is None


def test_hard_close_guard_blocks_card(state_paths, monkeypatch):
    """Cards are blocked at or after the immutable 20:00 UTC hard close."""
    execution, _, _, _ = state_paths

    class FrozenDateTime(datetime):
        """Freeze execution time at 20:30 UTC."""

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 22, 20, 30, tzinfo=UTC)

    monkeypatch.setattr(execution, "datetime", FrozenDateTime)
    assert execution.generate_card(_setup()) is None


def test_20_00_research_is_cache_only(monkeypatch):
    """Cache-only research must not invoke planning or execution-card generation."""
    from bybit_bot import data_aggregator, planning, research

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
    monkeypatch.setattr(research, "_save_research", lambda result: None)
    monkeypatch.setattr(research.telegram, "send_message", lambda message: True)
    called: list[dict] = []
    monkeypatch.setattr(planning, "run_planning", lambda result: called.append(result))

    research.run_research(cache_only=True)
    assert called == []


def test_filled_preserves_planned_entry(state_paths):
    """Manual fill confirmation retains the entry originally shown on the card."""
    _, _, orchestrator, order_path = state_paths
    order_path.write_text(json.dumps([{"symbol": "GOODUSDT", "status": "PENDING", "entry": 100.0}]), encoding="utf-8")
    orchestrator.handle_filled("GOOD", 101.5, "12:00")
    assert json.loads(order_path.read_text(encoding="utf-8"))[0]["planned_entry"] == 100.0


def test_filled_stores_fill_price(state_paths):
    """Manual fill confirmation persists the user-reported actual fill price."""
    _, _, orchestrator, order_path = state_paths
    order_path.write_text(json.dumps([{"symbol": "GOODUSDT", "status": "PENDING", "entry": 100.0}]), encoding="utf-8")
    orchestrator.handle_filled("GOOD", 101.5, "12:00")
    assert json.loads(order_path.read_text(encoding="utf-8"))[0]["fill_price"] == 101.5


def test_pnl_uses_fill_price_over_planned_entry(state_paths):
    """P&L uses the actual fill when it is available on a filled order."""
    _, monitor, _, _ = state_paths
    order = {"symbol": "GOODUSDT", "side": "LONG", "entry": 100.0, "fill_price": 110.0, "notional": 110.0}
    assert monitor.calculate_pnl(order, 121.0) == 11.0


def test_minimum_stop_guard_rejects_tight_stop(state_paths):
    """A data-anomalous stop below the approved safety floor is rejected."""
    execution, _, _, _ = state_paths
    assert execution.generate_card(_setup(stop=0.1)) is None


def test_concurrent_writes_no_lost_update(state_paths):
    """Independent concurrent writers must preserve both PENDING records."""
    execution, _, _, order_path = state_paths
    cards = [
        {"symbol": "FIRSTUSDT", "status": "PENDING", "expiry_utc": "2026-09-22T12:00:00+00:00"},
        {"symbol": "SECONDUSDT", "status": "PENDING", "expiry_utc": "2026-09-22T12:00:00+00:00"},
    ]
    threads = [threading.Thread(target=execution.save_pending_order, args=(card,)) for card in cards]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saved = json.loads(order_path.read_text(encoding="utf-8"))
    assert {order["symbol"] for order in saved} == {"FIRSTUSDT", "SECONDUSDT"}
