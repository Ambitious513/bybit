"""TASK-022 advanced monitor checks tested through isolated monitor cycles."""

import json
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set environment before importing monitor and its dependencies."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


@pytest.fixture
def monitor_state(tmp_path, monkeypatch):
    """Redirect all monitor runtime files to a temporary state directory."""
    from bybit_bot import monitor
    monkeypatch.setattr(monitor, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "_ACTIVE_ORDERS_PATH", str(tmp_path / "active_orders.json"))
    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor, "_PAPER_BALANCE_PATH", str(tmp_path / "paper_balance.json"))
    monkeypatch.setattr(monitor, "_RESEARCH_CACHE_PATH", str(tmp_path / "research_cache.json"))
    return monitor, tmp_path


def _pending(**changes):
    order = {
        "symbol": "TAOUSDT", "status": "PENDING", "side": "LONG", "zone_bottom": 100.0, "zone_top": 105.0,
        "expiry_utc": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    order.update(changes)
    return order


def _filled(**changes):
    order = {
        "symbol": "COINUSDT", "status": "FILLED", "side": "LONG", "tag": "TRADFI", "entry": 100.0,
        "sl": 95.0, "tp1": 105.0, "tp2": 110.0, "tp3": 115.0, "notional": 100.0,
        "core_closed": False, "runner_closed": False, "fill_time_utc": datetime.now(UTC).isoformat(),
    }
    order.update(changes)
    return order


def _raw_candles(closes):
    return [[index, "100", str(close + 1), str(close - 1), str(close), "10", "1000"] for index, close in enumerate(closes)]


def test_weekend_escalation_fires_sat(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    (state / "active_orders.json").write_text(json.dumps([_pending(expiry_utc=(now + timedelta(hours=1)).isoformat())]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: now)
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "110"})
    monkeypatch.setattr(monitor.bybit_api, "get_klines", lambda *args: _raw_candles([106, 107, 108]))
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert any("WEEKEND ESCALATION" in message for message in sent)


def test_weekend_escalation_no_fire_weekday(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_pending()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 12, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "110"})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert not any("WEEKEND ESCALATION" in message for message in sent)


def test_dead_cat_in_zone_passed(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_pending()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 12, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "102"})
    monkeypatch.setattr(monitor.sr_calculator, "dead_cat_check", lambda *args: {"passed": True})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert any("ENTRY CONFIRMED" in message for message in sent)


def test_dead_cat_in_zone_failed(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_pending()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 12, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "102"})
    # New flat-boolean format: bounce_volume_ok=False simulates Phase 2 volume failure.
    monkeypatch.setattr(monitor.sr_calculator, "dead_cat_check",
                        lambda *args: {"passed": False, "bounce_volume_ok": False,
                                       "bounce_above_midpoint": True, "higher_low": True,
                                       "drop_was_strong": False})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert any("DEAD CAT WARNING" in message and "bounce volume" in message for message in sent)


def test_dead_cat_not_triggered_outside_zone(monitor_state, monkeypatch):
    monitor, state = monitor_state
    (state / "active_orders.json").write_text(json.dumps([_pending()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 12, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "106"})
    called: list[bool] = []
    monkeypatch.setattr(monitor.sr_calculator, "dead_cat_check", lambda *args: called.append(True))
    monitor.run_monitor_cycle()
    assert called == []


def test_thin_volume_tradfi_fires(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_filled()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 19, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "95.2" if symbol == "COINUSDT" else "65000"})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert any("THIN LIQUIDITY WARNING" in message for message in sent)


def test_thin_volume_no_fire_crypto(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_filled(tag="CRYPTO")]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 21, 19, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "95.2" if symbol == "COINUSDT" else "65000"})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert not any("THIN LIQUIDITY WARNING" in message for message in sent)


def test_thin_volume_no_fire_weekend(monitor_state, monkeypatch):
    monitor, state = monitor_state
    sent: list[str] = []
    (state / "active_orders.json").write_text(json.dumps([_filled()]), encoding="utf-8")
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 26, 19, tzinfo=UTC))
    monkeypatch.setattr(monitor.bybit_api, "get_ticker", lambda symbol: {"price": "95.2" if symbol == "COINUSDT" else "65000"})
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    monitor.run_monitor_cycle()
    assert not any("THIN LIQUIDITY WARNING" in message for message in sent)
