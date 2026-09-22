"""TASK-018 deterministic S/R calculator tests."""

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
    """Import the calculator after test environment setup."""
    from bybit_bot import sr_calculator
    return sr_calculator


def _candle(index, high=None, low=None, close=None, volume=10.0):
    low = float(index - 1 if low is None else low)
    high = float(index + 1 if high is None else high)
    close = float(index if close is None else close)
    return {"ts": index, "open": close, "high": high, "low": low, "close": close, "volume": volume, "turnover": volume * close}


def _raw(candles):
    """Make foundation-wrapper rows in chronological (oldest-first) order."""
    return [[c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"], c["turnover"]] for c in candles]


def test_bollinger_bands_returns_6_levels():
    calculator = _module()
    candles = [_candle(index, close=100 + index) for index in range(25)]
    result = calculator._bollinger_bands(candles, candles, candles)
    assert len(result["supports"]) == 6
    assert len(result["resistances"]) == 6


def test_swing_highs_detect_local_maxima():
    calculator = _module()
    candles = [_candle(index, high=high, low=20 - high, close=10) for index, high in enumerate([1, 2, 3, 9, 3, 2, 1])]
    result = calculator._swing_highs_lows(candles, [], [])
    assert any(level["level"] == 9 for level in result["resistances"])


def test_volume_profile_finds_high_volume_zones():
    calculator = _module()
    candles = [_candle(index, high=111, low=89, close=90, volume=100) for index in range(10)]
    candles += [_candle(index + 10, high=111, low=89, close=110, volume=120) for index in range(10)]
    candles.append(_candle(21, high=111, low=89, close=100, volume=1))
    result = calculator._volume_profile(candles)
    assert result["supports"] and result["resistances"]
    assert all(level["weight"] == 1.5 for level in [*result["supports"], *result["resistances"]])


def test_cluster_groups_within_tolerance():
    calculator = _module()
    result = calculator._cluster_levels([
        {"level": 100.0, "method": "a", "weight": 1.0},
        {"level": 100.3, "method": "b", "weight": 1.0},
    ], 100.0)
    assert len(result) == 1
    assert result[0]["count"] == 2


def test_cluster_separates_outside_tolerance():
    calculator = _module()
    result = calculator._cluster_levels([
        {"level": 100.0, "method": "a", "weight": 1.0},
        {"level": 101.5, "method": "b", "weight": 1.0},
    ], 100.0)
    assert len(result) == 2


def test_best_entry_zone_below_price():
    calculator = _module()
    zone = calculator._best_entry_zone([{"top": 99, "bottom": 98, "score": 2.0}], 100.0)
    assert zone["top"] < 100.0


def test_find_sl_below_entry_zone():
    calculator = _module()
    sl = calculator._find_sl([{"top": 95, "bottom": 94, "score": 1.0}], 98.0)
    assert sl < 98.0


def test_get_sr_levels_returns_valid_geometry(monkeypatch):
    calculator = _module()
    candles = [_candle(index, high=111, low=89, close=90 + index * .1, volume=10) for index in range(200)]
    monkeypatch.setattr(calculator.bybit_api, "get_klines", lambda *args: _raw(candles))
    result = calculator.get_sr_levels("TESTUSDT", 100.0)
    assert result["entry_zone_top"] > result["entry_zone_bottom"]
    assert result["sl_level"] < result["entry_zone_bottom"]
    assert all(level > 100.0 for level in result["resistances"])


@pytest.mark.parametrize(
    ("candidate", "assertion"),
    [
        ({"high": 110, "low": 100, "close": 104, "volume": 10}, lambda result: not result["rule1_midpoint"]["pass"]),
        ({"high": 110, "low": 101, "close": 108, "volume": 4}, lambda result: not result["rule2_volume"]["pass"]),
        ({"high": 110, "low": 99, "close": 108, "volume": 10}, lambda result: not result["rule3_higher_low"]["pass"]),
    ],
)
def test_dead_cat_rule_failures(monkeypatch, candidate, assertion):
    calculator = _module()
    candles = [_candle(index, high=109, low=100, close=106, volume=10) for index in range(3)]
    candles.append(_candle(3, **candidate))
    candles.append(_candle(4, high=110, low=102, close=108, volume=10))
    monkeypatch.setattr(calculator.bybit_api, "get_klines", lambda *args: _raw(candles))
    assert assertion(calculator.dead_cat_check("TESTUSDT", 100.0))


def test_dead_cat_all_pass(monkeypatch):
    calculator = _module()
    candles = [_candle(index, high=109, low=100, close=106, volume=10) for index in range(3)]
    candles.append(_candle(3, high=110, low=101, close=108, volume=10))
    candles.append(_candle(4, high=111, low=102, close=109, volume=10))
    monkeypatch.setattr(calculator.bybit_api, "get_klines", lambda *args: _raw(candles))
    result = calculator.dead_cat_check("TESTUSDT", 100.0)
    assert result["passed"] is True
