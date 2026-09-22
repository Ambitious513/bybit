"""TASK-020 core monitor tests using isolated paper-state files."""

import json
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set runtime configuration before monitor imports occur."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


@pytest.fixture
def monitor_paths(tmp_path, monkeypatch):
    """Direct all persistent monitor state to an isolated temporary directory."""
    from bybit_bot import monitor
    monkeypatch.setattr(monitor, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "_ACTIVE_ORDERS_PATH", str(tmp_path / "active_orders.json"))
    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor, "_PAPER_BALANCE_PATH", str(tmp_path / "paper_balance.json"))
    monkeypatch.setattr(monitor, "_RESEARCH_CACHE_PATH", str(tmp_path / "research_cache.json"))
    monkeypatch.setattr(monitor.telegram, "send_message", lambda message: True)
    return monitor, tmp_path


def _order(side="LONG", status="FILLED", **overrides):
    order = {
        "symbol": "GOODUSDT", "side": side, "status": status, "entry": 100.0, "sl": 95.0 if side == "LONG" else 105.0,
        "tp1": 105.0 if side == "LONG" else 95.0, "tp2": 110.0 if side == "LONG" else 90.0,
        "tp3": 115.0 if side == "LONG" else 85.0, "notional": 100.0, "paper_risk": 2.0,
        "core_closed": False, "runner_closed": False, "fill_time_utc": datetime.now(UTC).isoformat(),
        "expiry_utc": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "score": 50,
    }
    order.update(overrides)
    return order


def test_empty_orders_returns_immediately(monitor_paths, caplog):
    monitor, _ = monitor_paths
    caplog.set_level("INFO", logger="monitor")
    monitor.run_monitor_cycle()
    assert "no_open_orders" in caplog.text


def test_pending_expired_sets_expired_status(monitor_paths, monkeypatch):
    monitor, _ = monitor_paths
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    monkeypatch.setattr(monitor, "_utc_now", lambda: now)
    alerts, updated = monitor.check_order(_order(status="PENDING", expiry_utc=(now - timedelta(seconds=1)).isoformat()), 0, 0, "BULLISH")
    assert updated["status"] == "EXPIRED"
    assert "ORDER EXPIRED" in alerts[0]


def test_sl_hit_long(monitor_paths):
    monitor, paths = monitor_paths
    alerts, updated = monitor.check_order(_order(), 95.0, 65000.0, "BULLISH")
    assert updated["status"] == "CLOSED"
    assert "SL HIT" in alerts[0]
    assert json.loads((paths / "trade_log.json").read_text())[0]["exit_reason"] == "SL"


def test_sl_hit_short(monitor_paths):
    monitor, _ = monitor_paths
    alerts, updated = monitor.check_order(_order("SHORT"), 105.0, 65000.0, "BULLISH")
    assert updated["status"] == "CLOSED"
    assert "SL HIT" in alerts[0]


def test_tp1_hit_moves_sl_to_entry(monitor_paths):
    monitor, _ = monitor_paths
    alerts, updated = monitor.check_order(_order(), 105.0, 65000.0, "BULLISH")
    assert updated["core_closed"] is True
    assert updated["sl"] == updated["entry"]
    assert any("TP1 HIT" in alert for alert in alerts)


def test_tp2_fires_only_after_core_closed(monitor_paths):
    monitor, _ = monitor_paths
    no_alerts, _ = monitor.check_order(_order(tp1=120.0, tp2=110.0, tp3=130.0), 110.0, 65000.0, "BULLISH")
    alerts, _ = monitor.check_order(_order(core_closed=True, sl=100.0), 110.0, 65000.0, "BULLISH")
    assert not any("TP2 HIT" in alert for alert in no_alerts)
    assert any("TP2 HIT" in alert for alert in alerts)


def test_tp3_closes_position(monitor_paths):
    monitor, _ = monitor_paths
    alerts, updated = monitor.check_order(_order(core_closed=True, sl=100.0), 115.0, 65000.0, "BULLISH")
    assert updated["runner_closed"] is True
    assert updated["status"] == "CLOSED"
    assert any("TP3 HIT" in alert for alert in alerts)


def test_time_stop_fires_after_2h(monitor_paths, monkeypatch):
    monitor, _ = monitor_paths
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    monkeypatch.setattr(monitor, "_utc_now", lambda: now)
    alerts, _ = monitor.check_order(_order(fill_time_utc=(now - timedelta(hours=3)).isoformat()), 100.0, 65000.0, "BULLISH")
    assert any("TIME-STOP" in alert for alert in alerts)


def test_hard_close_fires_at_20_utc(monitor_paths, monkeypatch):
    monitor, _ = monitor_paths
    monkeypatch.setattr(monitor, "_utc_now", lambda: datetime(2026, 9, 22, 20, tzinfo=UTC))
    alerts, _ = monitor.check_order(_order(), 100.0, 65000.0, "BULLISH")
    assert any("HARD CLOSE" in alert for alert in alerts)


def test_btc_regime_flip_for_long(monitor_paths):
    monitor, _ = monitor_paths
    alerts, _ = monitor.check_order(_order(), 100.0, 65000.0, "BEARISH")
    assert any("REGIME FLIP" in alert for alert in alerts)


def test_btc_regime_flip_no_alert_for_short(monitor_paths):
    monitor, _ = monitor_paths
    alerts, _ = monitor.check_order(_order("SHORT"), 100.0, 65000.0, "BULLISH")
    assert not any("REGIME FLIP" in alert for alert in alerts)


def test_regime_cache_without_btc_support_is_handled(monitor_paths):
    monitor, paths = monitor_paths
    (paths / "research_cache.json").write_text(
        json.dumps({"timestamp": datetime.now(UTC).isoformat(), "regime": {"regime": "BULLISH"}}),
        encoding="utf-8",
    )
    assert monitor.load_btc_regime_from_cache() == "BULLISH"


def test_calculate_pnl_long_profit(monitor_paths):
    monitor, _ = monitor_paths
    assert monitor.calculate_pnl(_order(), 110.0) == 10.0


def test_calculate_pnl_short_profit(monitor_paths):
    monitor, _ = monitor_paths
    assert monitor.calculate_pnl(_order("SHORT"), 90.0) == 10.0


def test_balance_warning_below_floor(monitor_paths, monkeypatch):
    monitor, paths = monitor_paths
    sent: list[str] = []
    monkeypatch.setattr(monitor.telegram, "send_message", sent.append)
    (paths / "paper_balance.json").write_text(json.dumps({"balance": 13.50, "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0}), encoding="utf-8")
    monitor.update_paper_balance(-1.0)
    assert any("BALANCE WARNING" in message for message in sent)


def test_log_trade_appends_to_file(monitor_paths):
    monitor, paths = monitor_paths
    monitor.log_trade(_order(), 110.0, "TP3")
    monitor.log_trade(_order(), 95.0, "SL")
    assert len(json.loads((paths / "trade_log.json").read_text(encoding="utf-8"))) == 2
