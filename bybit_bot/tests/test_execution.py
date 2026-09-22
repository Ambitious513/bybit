"""TASK-019 execution-card tests; all Telegram and filesystem state is isolated."""

import json

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set module configuration before importing execution dependencies."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _module():
    """Import execution after environment setup."""
    from bybit_bot import execution
    return execution


def _context(tmp_path, monkeypatch, direction="LONG"):
    """Provide the research context that supplies the direction contract."""
    execution = _module()
    cache_path = tmp_path / "research_cache.json"
    order_path = tmp_path / "active_orders.json"
    cache_path.write_text(json.dumps({"regime": {"direction": direction, "regime": "BULLISH", "btc_price": 65000}}), encoding="utf-8")
    monkeypatch.setattr(execution, "_RESEARCH_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(execution, "_ACTIVE_ORDERS_PATH", str(order_path))
    monkeypatch.setattr(execution, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(execution.telegram, "send_card", lambda lines: True)
    return execution, order_path


def _setup(stop=3.0, tag="GAINER", current_price=100.0, short=False):
    entry = 98.0
    return {
        "symbol": "GOODUSDT", "tag": tag, "current_price": current_price,
        "flow": {"longShortRatio": 2.0, "fundSide": "Bullish"}, "news": {}, "score": 50,
        "sr": {
            "entry_mid": entry, "entry_zone_top": 97.0, "entry_zone_bottom": 96.0,
            "sl_level": entry * (1 + stop / 100) if short else entry * (1 - stop / 100),
            "stop_dist_pct": stop,
        },
    }


def test_stop_too_wide_returns_none(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch)
    assert execution.generate_card(_setup(stop=9.0)) is None


@pytest.mark.parametrize(("stop", "leverage"), [(0.8, 10), (3.0, 5), (6.0, 3)])
def test_leverage_table(tmp_path, monkeypatch, stop, leverage):
    execution, _ = _context(tmp_path, monkeypatch)
    assert execution.generate_card(_setup(stop=stop))["leverage"] == leverage


def test_tp_long_above_entry(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch, "LONG")
    card = execution.generate_card(_setup())
    assert card["tp1"] > card["entry"] > card["sl"]


def test_tp_short_below_entry(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch, "SHORT")
    card = execution.generate_card(_setup(short=True))
    assert card["tp1"] < card["entry"] < card["sl"]


def test_tradfi_tp_multiplier(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch)
    card = execution.generate_card(_setup(stop=3.0, tag="TRADFI"))
    assert card["tp1"] == pytest.approx(card["entry"] * (1 + .03 * .8))


def test_crypto_tp_multiplier(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch)
    card = execution.generate_card(_setup(stop=3.0))
    assert card["tp1"] == pytest.approx(card["entry"] * 1.03)


@pytest.mark.parametrize(("current_price", "label", "minutes"), [(97.5, "URGENT", 10), (99.0, "PATIENT", 30), (101.0, "SET & FORGET", 60)])
def test_validity_windows(tmp_path, monkeypatch, current_price, label, minutes):
    execution, _ = _context(tmp_path, monkeypatch)
    card = execution.generate_card(_setup(current_price=current_price))
    assert (card["window_label"], card["window_mins"]) == (label, minutes)


def test_active_orders_json_appends(tmp_path, monkeypatch):
    execution, order_path = _context(tmp_path, monkeypatch)
    execution.generate_card(_setup())
    execution.generate_card(_setup(stop=4.0))
    assert len(json.loads(order_path.read_text(encoding="utf-8"))) == 2


def test_card_is_sent_in_enclosed_monospace_rows(tmp_path, monkeypatch):
    execution, _ = _context(tmp_path, monkeypatch)
    captured: list[list[str]] = []
    monkeypatch.setattr(execution.telegram, "send_card", lambda lines: captured.append(lines) or True)
    execution.generate_card(_setup())
    assert captured[0][0].startswith("╔")
    assert all(line.endswith("║") for line in captured[0] if line.startswith("║"))
    assert captured[0][-1].startswith("╚")


def test_atomic_write_preserves_existing_file_on_replace_failure(tmp_path, monkeypatch):
    execution, order_path = _context(tmp_path, monkeypatch)
    original = [{"symbol": "EXISTINGUSDT", "status": "PENDING"}]
    order_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(execution.os, "replace", lambda source, destination: (_ for _ in ()).throw(OSError("interrupted")))
    execution.save_pending_order({"symbol": "NEWUSDT", "expiry_utc": "2026-01-01T00:00:00+00:00"})
    assert json.loads(order_path.read_text(encoding="utf-8")) == original
