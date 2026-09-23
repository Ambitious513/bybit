"""TASK-026 Pre-GATE-3 hardening tests.

Covers:
  R1 — Dead cat Phase 1 drop-volume filter
  R2 — /filled price validation
  R3 — TRADFI perps startup audit
  R5 — Connection pool / quickscan worker cap
  R6 — Funding creep alert (once per order)
"""
from __future__ import annotations

import json

import pytest


# ── Environment fixture ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY",      "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET",   "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID",   "999")
    monkeypatch.setenv("OPENROUTER_KEY_1",   "or1")
    monkeypatch.setenv("OPENROUTER_KEY_2",   "or2")
    monkeypatch.setenv("OPENROUTER_KEY_3",   "or3")


# ── Candle helpers ────────────────────────────────────────────────────────────

def _candle(ts, *, high, low, close, volume, open_=None):
    """Build a single OHLCV dict.  Default open == close (doji, not red)."""
    o = open_ if open_ is not None else close
    return {
        "ts": ts, "open": float(o), "high": float(high),
        "low": float(low), "close": float(close),
        "volume": float(volume), "turnover": float(volume * close),
    }


def _raw(candles):
    """Oldest-first raw kline format expected by _api_candles()."""
    return [
        [c["ts"], c["open"], c["high"], c["low"],
         c["close"], c["volume"], c["turnover"]]
        for c in candles
    ]


def _make_candles_with_ratio(target_ratio: float, bounce_cfg: dict,
                              base_vol: float = 10.0) -> list:
    """Build 5 baseline candles + 1 bounce achieving target_ratio.

    Strategy: 4 green (doji) candles at ``base_vol`` + 1 red candle whose
    volume is chosen so that:

        drop_vol_ratio = red_vol / mean(all_baseline_vols) = target_ratio

    Solving: red_vol = target_ratio * 4 * base_vol / (5 - target_ratio)

    Valid for target_ratio < 5.  For target_ratio == 0, all candles are green
    (no red candles → drop_vol = 0 → ratio = 0).
    """
    candles: list = []
    if target_ratio == 0.0:
        for i in range(5):
            candles.append(_candle(i, high=110, low=100, close=106, volume=base_vol))
    else:
        red_vol = target_ratio * 4 * base_vol / (5 - target_ratio)
        for i in range(4):
            # Green / doji candles (open == close → not red)
            candles.append(_candle(i, high=110, low=100, close=106, volume=base_vol))
        # Red candle: close < open
        candles.append(_candle(4, high=110, low=100, close=103,
                               volume=red_vol, open_=107))
    candles.append(_candle(5, **bounce_cfg))
    return candles


def _sr():
    from bybit_bot import sr_calculator
    return sr_calculator


def _orc():
    import bybit_bot.orchestrator as m
    return m


def _mon():
    import bybit_bot.monitor as m
    return m


# ─────────────────────────────────────────────────────────────────────────────
# R1 — Dead Cat Phase 1 (drop-volume filter)
# ─────────────────────────────────────────────────────────────────────────────

_CLEAN_BOUNCE = dict(high=111, low=102, close=109, volume=10)


def test_dead_cat_phase1_high_vol_drop_blocks_entry(monkeypatch):
    """drop_vol_ratio ≈ 2.5x (> 1.30) → Phase 1 fails → passed=False."""
    sr = _sr()
    candles = _make_candles_with_ratio(2.5, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["passed"] is False
    assert result["drop_was_strong"] is True
    assert result["warning"] is not None


def test_dead_cat_phase1_low_vol_drop_allows_phase2(monkeypatch):
    """drop_vol_ratio ≈ 0.5x (< 0.70) → Phase 1 passes; clean Phase 2 → passed=True."""
    sr = _sr()
    candles = _make_candles_with_ratio(0.5, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["drop_was_weak"] is True
    assert result["passed"] is True


def test_dead_cat_phase1_no_red_candles_treated_as_weak(monkeypatch):
    """No red candles in baseline → drop_vol=0 → drop_was_weak=True → Phase 1 passes."""
    sr = _sr()
    candles = _make_candles_with_ratio(0.0, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["drop_was_weak"] is True
    assert result["drop_vol_ratio"] == 0.0


def test_dead_cat_phase1_neutral_vol_clean_phase2_passes(monkeypatch):
    """Neutral ratio 1.0 (0.70–1.30) + clean Phase 2 → passed=True."""
    sr = _sr()
    candles = _make_candles_with_ratio(1.0, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["drop_was_strong"] is False
    assert result["drop_was_weak"] is False
    assert result["passed"] is True


def test_dead_cat_phase1_neutral_vol_failed_phase2_blocks(monkeypatch):
    """Neutral ratio 1.0 + Phase 2 failure (close < midpoint) → passed=False."""
    sr = _sr()
    # Bounce: high=110, low=100 → midpoint=105; close=104 < 105 → Phase 2 fails
    bad_bounce = dict(high=110, low=100, close=104, volume=10)
    candles = _make_candles_with_ratio(1.0, bad_bounce)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["drop_was_strong"] is False
    assert result["bounce_above_midpoint"] is False
    assert result["passed"] is False


def test_dead_cat_warning_message_present_on_strong_drop(monkeypatch):
    """warning field contains 'HIGH VOLUME DROP' when ratio > 1.30."""
    sr = _sr()
    candles = _make_candles_with_ratio(2.0, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["warning"] is not None
    assert "HIGH VOLUME DROP" in result["warning"]


def test_dead_cat_all_phases_pass_on_clean_setup(monkeypatch):
    """Low drop ratio + clean Phase 2 → passed=True and warning=None."""
    sr = _sr()
    candles = _make_candles_with_ratio(0.4, _CLEAN_BOUNCE)
    monkeypatch.setattr(sr.bybit_api, "get_klines", lambda *a: _raw(candles))
    result = sr.dead_cat_check("TESTUSDT", 100.0)
    assert result["passed"] is True
    assert result["warning"] is None


# ─────────────────────────────────────────────────────────────────────────────
# R2 — /filled price validation
# ─────────────────────────────────────────────────────────────────────────────

def test_filled_rejects_zero_price(monkeypatch):
    """price=0 → rejection message sent before any lock or file I/O."""
    orc = _orc()
    sent = []
    monkeypatch.setattr(orc.telegram, "send_message", lambda msg, **kw: sent.append(msg))
    orc.handle_filled("LABUSDT", 0.0, "10:30")
    assert any("Invalid fill price" in m or "positive" in m for m in sent)


def test_filled_rejects_negative_price(monkeypatch):
    """price=-1.5 → rejection message sent before any lock or file I/O."""
    orc = _orc()
    sent = []
    monkeypatch.setattr(orc.telegram, "send_message", lambda msg, **kw: sent.append(msg))
    orc.handle_filled("LABUSDT", -1.5, "10:30")
    assert any("Invalid fill price" in m or "positive" in m for m in sent)


def test_filled_accepts_valid_price(monkeypatch):
    """price=0.06045 → no price-guard rejection; may produce 'No pending order' message."""
    orc = _orc()
    sent = []
    monkeypatch.setattr(orc.telegram, "send_message", lambda msg, **kw: sent.append(msg))
    # Stub I/O so the function completes without touching disk
    monkeypatch.setattr(orc, "load_active_orders", lambda: [])
    monkeypatch.setattr(orc, "_save_active_orders", lambda orders: True)
    orc.handle_filled("LABUSDT", 0.06045, "10:30")
    assert not any("Invalid fill price" in m for m in sent)


# ─────────────────────────────────────────────────────────────────────────────
# R3 — TRADFI perps startup audit
# ─────────────────────────────────────────────────────────────────────────────

def test_tradfi_audit_sends_warning_for_inactive_symbol(monkeypatch):
    """One inactive symbol → Telegram warning sent naming the symbol."""
    orc = _orc()
    sent = []
    monkeypatch.setattr(orc.telegram, "send_message", lambda msg, **kw: sent.append(msg))

    def mock_ticker(symbol):
        return None if symbol == "MSTRUSDT" else {"price": "100.0"}

    monkeypatch.setattr(orc.bybit_api, "get_ticker", mock_ticker)
    orc.audit_tradfi_perps()
    assert any("MSTRUSDT" in m for m in sent)
    assert any("INACTIVE" in m for m in sent)


def test_tradfi_audit_silent_when_all_active(monkeypatch):
    """All symbols active → no Telegram message sent."""
    orc = _orc()
    sent = []
    monkeypatch.setattr(orc.telegram, "send_message", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(orc.bybit_api, "get_ticker",
                        lambda sym: {"price": "100.0", "lastPrice": "100.0"})
    orc.audit_tradfi_perps()
    assert len(sent) == 0


# ─────────────────────────────────────────────────────────────────────────────
# R5 — Quickscan worker cap
# ─────────────────────────────────────────────────────────────────────────────

def test_quickscan_executor_respects_max_workers():
    """quickscan.py uses QUICKSCAN_MAX_WORKERS constant for ThreadPoolExecutor cap."""
    from bybit_bot.config import QUICKSCAN_MAX_WORKERS
    assert QUICKSCAN_MAX_WORKERS == 8
    import bybit_bot.quickscan as qs
    import inspect
    src = inspect.getsource(qs)
    assert "QUICKSCAN_MAX_WORKERS" in src
    assert "max_workers=QUICKSCAN_MAX_WORKERS" in src


# ─────────────────────────────────────────────────────────────────────────────
# R6 — Funding creep alert (once per order)
# ─────────────────────────────────────────────────────────────────────────────

def test_funding_creep_alert_fires_once():
    """First call returns a warning string; after funding_warned=True it returns None."""
    mon = _mon()
    order: dict = {"symbol": "LABUSDT", "status": "FILLED"}
    warning1 = mon.check_funding_creep(order, current_funding=0.0010)
    assert warning1 is not None
    # Caller sets flag after firing
    order["funding_warned"] = True
    warning2 = mon.check_funding_creep(order, current_funding=0.0010)
    assert warning2 is None


def test_funding_creep_no_alert_below_threshold():
    """funding=0.0003 (< 0.0005 threshold) → no alert."""
    mon = _mon()
    order: dict = {"symbol": "LABUSDT", "status": "FILLED"}
    warning = mon.check_funding_creep(order, current_funding=0.0003)
    assert warning is None
