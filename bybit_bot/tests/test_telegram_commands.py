"""TASK-022 authenticated command handler tests with isolated JSON state."""

import json
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide command runtime configuration before module imports."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


@pytest.fixture
def command_state(tmp_path, monkeypatch):
    """Point orchestrator and monitor state readers/writers at one temp directory."""
    from bybit_bot import monitor, orchestrator
    monkeypatch.setattr(orchestrator, "_bot_data_path", lambda filename: str(tmp_path / filename))
    monkeypatch.setattr(orchestrator, "TELEGRAM_CHAT_ID", "999")
    monkeypatch.setattr(orchestrator.telegram, "send_message", lambda message: True)
    monkeypatch.setattr(orchestrator.telegram, "send_card", lambda lines: True)
    monkeypatch.setattr(monitor, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor, "_ACTIVE_ORDERS_PATH", str(tmp_path / "active_orders.json"))
    monkeypatch.setattr(monitor, "_TRADE_LOG_PATH", str(tmp_path / "trade_log.json"))
    monkeypatch.setattr(monitor, "_PAPER_BALANCE_PATH", str(tmp_path / "paper_balance.json"))
    return orchestrator, monitor, tmp_path


def _order(status="PENDING"):
    return {
        "symbol": "LABUSDT", "status": status, "side": "LONG", "entry": 0.057, "sl": 0.055,
        "tp1": 0.059, "notional": 100.0, "paper_risk": 2.0, "score": 50,
        "fill_time_utc": None, "expiry_utc": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }


def test_handle_filled_updates_status(command_state):
    orchestrator, _, state = command_state
    (state / "active_orders.json").write_text(json.dumps([_order()]), encoding="utf-8")
    orchestrator.handle_filled("LAB", 0.0571, "14:32")
    saved = json.loads((state / "active_orders.json").read_text(encoding="utf-8"))[0]
    assert saved["status"] == "FILLED"
    assert saved["fill_time_utc"]


def test_handle_filled_unknown_symbol(command_state, monkeypatch):
    orchestrator, _, state = command_state
    sent: list[str] = []
    (state / "active_orders.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(orchestrator.telegram, "send_message", sent.append)
    orchestrator.handle_filled("UNKNOWN", 1.0, "14:32")
    assert "No pending order" in sent[0]


def test_handle_closed_calculates_pnl(command_state):
    orchestrator, _, state = command_state
    (state / "active_orders.json").write_text(json.dumps([_order("FILLED")]), encoding="utf-8")
    orchestrator.handle_closed("LAB", 0.058)
    saved = json.loads((state / "active_orders.json").read_text(encoding="utf-8"))[0]
    balance = json.loads((state / "paper_balance.json").read_text(encoding="utf-8"))
    assert saved["status"] == "CLOSED"
    assert balance["total_trades"] == 1
    assert balance["total_pnl"] > 0


def test_handle_cancel_removes_pending(command_state):
    orchestrator, _, state = command_state
    (state / "active_orders.json").write_text(json.dumps([_order()]), encoding="utf-8")
    orchestrator.handle_cancel("LAB")
    assert json.loads((state / "active_orders.json").read_text(encoding="utf-8")) == []


def test_handle_skip_adds_to_session_list(command_state, monkeypatch):
    orchestrator, _, _ = command_state
    monkeypatch.setattr(orchestrator, "SESSION_SKIP_LIST", [])
    orchestrator.handle_skip("LAB")
    assert "LABUSDT" in orchestrator.SESSION_SKIP_LIST


def test_handle_unknown_chat_id_ignored(command_state, monkeypatch):
    orchestrator, _, _ = command_state
    called: list[bool] = []
    monkeypatch.setattr(orchestrator, "handle_help", lambda: called.append(True))
    orchestrator._dispatch_telegram_update({"update_id": 1, "message": {"chat": {"id": "other"}, "text": "/help"}})
    assert called == []


def test_handle_balance_zero_trades(command_state, monkeypatch):
    orchestrator, _, state = command_state
    sent: list[str] = []
    (state / "paper_balance.json").write_text(json.dumps({"balance": 18.66, "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0}), encoding="utf-8")
    monkeypatch.setattr(orchestrator.telegram, "send_message", sent.append)
    orchestrator.handle_balance()
    assert "Win Rate: 0%" in sent[0]
    assert "Avg P&amp;L: $+0.00" in sent[0]


def test_handle_balance_shows_optional_core_runner_breakdown(command_state, monkeypatch):
    orchestrator, _, state = command_state
    sent: list[str] = []
    (state / "paper_balance.json").write_text(json.dumps({"balance": 20, "total_trades": 1, "wins": 1, "losses": 0, "total_pnl": 1.34}), encoding="utf-8")
    (state / "trade_log.json").write_text(json.dumps([{
        "entry": 100, "tp1": 102, "tp3": 105, "notional": 100, "side": "LONG",
    }]), encoding="utf-8")
    monkeypatch.setattr(orchestrator.telegram, "send_message", sent.append)
    orchestrator.handle_balance()
    assert "Approx. core at TP1: $+1.00 | runner at TP3: $+2.50" in sent[0]


def test_handle_market_generates_reduced_risk_card(command_state, monkeypatch):
    orchestrator, _, _ = command_state
    from bybit_bot import data_aggregator, execution, sr_calculator
    captured: list[float] = []
    monkeypatch.setattr(orchestrator.bybit_api, "get_ticker", lambda symbol: {"price": "100"})
    monkeypatch.setattr(data_aggregator, "get_capital_flow", lambda symbol: {"longShortRatio": 2.0, "fundSide": "Bullish"})
    monkeypatch.setattr(sr_calculator, "get_sr_levels", lambda symbol, price: {"sl_level": 95.0})
    monkeypatch.setattr(execution, "generate_card", lambda setup: captured.append(execution.PAPER_RISK_PER_TRADE) or {"symbol": setup["symbol"]})
    orchestrator.handle_market("LAB")
    assert captured == [1.0]
