"""TASK-018 planning and scoring tests."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set configuration placeholders before runtime modules are imported."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _module():
    """Import planning after test setup."""
    from bybit_bot import planning
    return planning


def _flow(whale=2.0, fund_side="Bullish", funding=0.0002):
    return {
        "longShortRatio": whale, "fundSide": fund_side,
        "topTraderPositionRate": 1.0, "fundingRate": {"latest": funding},
        "openInterestHistory": {"current": 100.0, "thirtyDaysAgo": 100.0},
    }


def _sr(symbol, current_price):
    return {
        "symbol": symbol, "current_price": current_price, "entry_zone_top": current_price * .99,
        "entry_zone_bottom": current_price * .98, "entry_mid": current_price * .985,
        "sl_level": current_price * .95, "stop_dist_pct": 3.5, "resistances": [current_price * 1.02],
        "confluence_score": 2.0, "gap_to_zone_pct": 1.0,
    }


def test_score_coin_unlock_disqualifies():
    planning = _module()
    assert planning.score_coin("GOODUSDT", _flow(), {"unlock_today": True}, "GAINER") == -999


def test_score_coin_whale_9_bullish():
    planning = _module()
    assert planning.score_coin("GOODUSDT", _flow(whale=9.0), {}, "GAINER") == 80


def test_score_coin_bearish_fund_penalty():
    planning = _module()
    score = planning.score_coin("GOODUSDT", _flow(whale=1.0, fund_side="Bearish", funding=.0002), {}, "GAINER")
    assert score == -25


def test_run_planning_returns_max_3(monkeypatch):
    planning = _module()
    research = {"qualified_coins": [
        {"symbol": f"COIN{index}USDT", "price": "100", "tag": "GAINER", "news": {}}
        for index in range(4)
    ]}
    monkeypatch.setattr(planning.data_aggregator, "get_capital_flow", lambda symbol: _flow(whale=float(symbol[4])))
    monkeypatch.setattr(planning.sr_calculator, "get_sr_levels", _sr)
    monkeypatch.setattr(planning, "_news_for_coin", lambda coin: {})
    monkeypatch.setattr(planning.telegram, "send_message", lambda message: True)
    assert len(planning.run_planning(research)) == 3


def test_run_planning_excludes_negative_scores(monkeypatch):
    planning = _module()
    research = {"qualified_coins": [
        {"symbol": "GOODUSDT", "price": "100", "tag": "GAINER", "news": {}},
        {"symbol": "BADUSDT", "price": "100", "tag": "GAINER", "news": {}},
    ]}
    monkeypatch.setattr(planning.data_aggregator, "get_capital_flow", lambda symbol: _flow())
    monkeypatch.setattr(planning.sr_calculator, "get_sr_levels", _sr)
    monkeypatch.setattr(planning, "_news_for_coin", lambda coin: {})
    monkeypatch.setattr(planning, "score_coin", lambda symbol, flow, news, tag: -1 if symbol == "BADUSDT" else 30)
    monkeypatch.setattr(planning.telegram, "send_message", lambda message: True)
    setups = planning.run_planning(research)
    assert [setup["symbol"] for setup in setups] == ["GOODUSDT"]
