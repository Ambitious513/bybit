"""Deterministic support/resistance calculation for read-only trade planning."""

import logging
from typing import Any

import numpy as np

from bybit_bot import bybit_api

logger = logging.getLogger("sr_calculator")


def _parse_candles(raw: list) -> list[dict]:
    """Parse newest-first Bybit kline rows into oldest-first OHLCV dictionaries."""
    parsed: list[dict] = []
    for row in reversed(raw):
        try:
            if len(row) < 7:
                raise ValueError("candle has fewer than seven fields")
            parsed.append({
                "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
                "turnover": float(row[6]),
            })
        except (IndexError, TypeError, ValueError) as exc:
            logger.warning("candle_parse_skipped error=%s", exc)
    return parsed


def _api_candles(raw: list) -> list[dict]:
    """Parse the foundation wrapper's oldest-first candle response correctly."""
    # ``bybit_api.get_klines`` already reverses Bybit's native newest-first data.
    return _parse_candles(list(reversed(raw))) if raw else []


def _bollinger_bands(
    ohlcv_5m: list[dict], ohlcv_4h: list[dict], ohlcv_1d: list[dict],
    period: int = 20, std_dev: float = 2.0,
) -> dict:
    """Calculate most-recent Bollinger support/resistance levels for three timeframes."""
    supports: list[dict] = []
    resistances: list[dict] = []
    for name, candles in (("5m", ohlcv_5m), ("4h", ohlcv_4h), ("1d", ohlcv_1d)):
        if len(candles) < period:
            logger.warning("bollinger_insufficient_candles timeframe=%s count=%d", name, len(candles))
            continue
        closes = np.array([candle["close"] for candle in candles[-period:]], dtype=float)
        middle = float(np.mean(closes))
        deviation = float(np.std(closes))
        supports.extend([
            {"level": middle - std_dev * deviation, "method": f"bb_lower_{name}", "weight": 1.0},
            {"level": middle, "method": f"bb_middle_{name}", "weight": 0.5},
        ])
        resistances.extend([
            {"level": middle + std_dev * deviation, "method": f"bb_upper_{name}", "weight": 1.0},
            {"level": middle, "method": f"bb_middle_{name}", "weight": 0.5},
        ])
    return {"supports": supports, "resistances": resistances}


def _swing_highs_lows(
    ohlcv_5m: list[dict], ohlcv_4h: list[dict], ohlcv_1d: list[dict], neighbors: int = 2,
) -> dict:
    """Find the five strongest recency-weighted swing highs and lows per timeframe."""
    supports: list[dict] = []
    resistances: list[dict] = []
    for name, candles, timeframe_weight in (
        ("5m", ohlcv_5m, 1.0), ("4h", ohlcv_4h, 1.5), ("1d", ohlcv_1d, 2.0),
    ):
        count = len(candles)
        if count < (2 * neighbors + 1):
            continue
        highs = [candle["high"] for candle in candles]
        lows = [candle["low"] for candle in candles]
        swing_highs: list[dict] = []
        swing_lows: list[dict] = []
        for index in range(neighbors, count - neighbors):
            recency_weight = index / count
            weight = timeframe_weight * (0.5 + 0.5 * recency_weight)
            if highs[index] == max(highs[index - neighbors:index + neighbors + 1]):
                swing_highs.append({"level": highs[index], "method": f"swing_high_{name}", "weight": weight})
            if lows[index] == min(lows[index - neighbors:index + neighbors + 1]):
                swing_lows.append({"level": lows[index], "method": f"swing_low_{name}", "weight": weight})
        resistances.extend(sorted(swing_highs, key=lambda item: item["weight"], reverse=True)[:5])
        supports.extend(sorted(swing_lows, key=lambda item: item["weight"], reverse=True)[:5])
    return {"supports": supports, "resistances": resistances}


def _volume_profile(ohlcv_5m: list[dict], bins: int = 20) -> dict:
    """Find accepted-price zones from the last 200 five-minute candles."""
    candles = ohlcv_5m[-200:]
    if not candles or bins <= 0:
        return {"supports": [], "resistances": []}
    current_price = candles[-1]["close"]
    low_price = min(candle["low"] for candle in candles)
    high_price = max(candle["high"] for candle in candles)
    if high_price <= low_price:
        return {"supports": [], "resistances": []}
    edges = np.linspace(low_price, high_price, bins + 1)
    volumes = np.zeros(bins, dtype=float)
    for candle in candles:
        index = min(int((candle["close"] - low_price) / (high_price - low_price) * bins), bins - 1)
        volumes[index] += candle["volume"]
    levels = [
        {"level": float((edges[index] + edges[index + 1]) / 2), "volume": float(volume)}
        for index, volume in enumerate(volumes)
        if volume > 0
    ]
    support_bins = sorted(
        (item for item in levels if item["level"] < current_price), key=lambda item: item["volume"], reverse=True
    )[:3]
    resistance_bins = sorted(
        (item for item in levels if item["level"] > current_price), key=lambda item: item["volume"], reverse=True
    )[:3]
    return {
        "supports": [{"level": item["level"], "method": "volume_profile", "weight": 1.5} for item in support_bins],
        "resistances": [{"level": item["level"], "method": "volume_profile", "weight": 1.5} for item in resistance_bins],
    }


def _summarize_cluster(cluster: list[dict]) -> dict:
    """Summarize a group of nearby S/R levels into one confluence zone."""
    total_weight = sum(float(item["weight"]) for item in cluster)
    weighted_level = (
        sum(float(item["level"]) * float(item["weight"]) for item in cluster) / total_weight
        if total_weight else 0.0
    )
    levels = [float(item["level"]) for item in cluster]
    return {
        "level": weighted_level,
        "top": max(levels),
        "bottom": min(levels),
        "score": total_weight,
        "methods": list(dict.fromkeys(str(item["method"]) for item in cluster)),
        "count": len(cluster),
    }


def _cluster_levels(levels: list[dict], current_price: float, tolerance_pct: float = 0.5) -> list[dict]:
    """Group levels whose prices are within the approved percentage tolerance."""
    if not levels or current_price <= 0:
        return []
    tolerance = current_price * tolerance_pct / 100
    ordered = sorted(levels, key=lambda item: float(item["level"]))
    clusters: list[list[dict]] = [[ordered[0]]]
    for level in ordered[1:]:
        current_cluster = clusters[-1]
        cluster_center = _summarize_cluster(current_cluster)["level"]
        if abs(float(level["level"]) - cluster_center) <= tolerance:
            current_cluster.append(level)
        else:
            clusters.append([level])
    return sorted((_summarize_cluster(cluster) for cluster in clusters), key=lambda item: item["level"])


def _best_entry_zone(support_clusters: list[dict], current_price: float) -> dict:
    """Select the strongest fully-below-price support confluence zone."""
    below_price = [cluster for cluster in support_clusters if cluster.get("top", 0.0) < current_price]
    if not below_price:
        return {"top": current_price * 0.99, "bottom": current_price * 0.98, "score": 0.0}
    return max(below_price, key=lambda cluster: (cluster.get("score", 0.0), cluster.get("level", 0.0)))


def _find_sl(support_clusters: list[dict], entry_zone_bottom: float) -> float:
    """Place the stop 0.3% beneath the deepest confirmed lower support."""
    confirmed = [cluster for cluster in support_clusters if cluster.get("top", 0.0) < entry_zone_bottom]
    if not confirmed:
        return entry_zone_bottom * 0.97
    deepest = min(confirmed, key=lambda cluster: cluster.get("bottom", float("inf")))
    return float(deepest["bottom"]) * (1 - 0.003)


def _top_resistances(resistance_clusters: list[dict], current_price: float, n: int = 3) -> list[float]:
    """Return up to ``n`` nearest resistance levels that are entirely above price."""
    above_price = [cluster for cluster in resistance_clusters if cluster.get("bottom", 0.0) > current_price]
    return [float(cluster["level"]) for cluster in sorted(above_price, key=lambda item: item["level"])[:n]]


def _empty_result(symbol: str) -> dict:
    """Return the safe S/R result used when market data is insufficient."""
    return {
        "symbol": symbol, "current_price": 0, "entry_zone_top": 0, "entry_zone_bottom": 0,
        "entry_mid": 0, "sl_level": 0, "stop_dist_pct": 0, "resistances": [],
        "confluence_score": 0, "gap_to_zone_pct": 0, "error": "insufficient_data",
    }


def get_sr_levels(symbol: str, current_price: float) -> dict:
    """Calculate confluence-based S/R zones from 5m, 4h, and daily candles."""
    if current_price <= 0:
        return _empty_result(symbol)
    raw_5m = bybit_api.get_klines(symbol, "5", 200)
    raw_4h = bybit_api.get_klines(symbol, "240", 100)
    raw_1d = bybit_api.get_klines(symbol, "D", 50)
    if not raw_5m or not raw_4h or not raw_1d:
        logger.warning("sr_insufficient_kline_data symbol=%s", symbol)
        return _empty_result(symbol)
    ohlcv_5m, ohlcv_4h, ohlcv_1d = _api_candles(raw_5m), _api_candles(raw_4h), _api_candles(raw_1d)
    if not ohlcv_5m or not ohlcv_4h or not ohlcv_1d:
        return _empty_result(symbol)
    bands = _bollinger_bands(ohlcv_5m, ohlcv_4h, ohlcv_1d)
    swings = _swing_highs_lows(ohlcv_5m, ohlcv_4h, ohlcv_1d)
    volume = _volume_profile(ohlcv_5m)
    support_clusters = _cluster_levels(
        [*bands["supports"], *swings["supports"], *volume["supports"]], current_price
    )
    resistance_clusters = _cluster_levels(
        [*bands["resistances"], *swings["resistances"], *volume["resistances"]], current_price
    )
    entry_zone = _best_entry_zone(support_clusters, current_price)
    entry_top = float(entry_zone["top"])
    entry_bottom = float(entry_zone["bottom"])
    if entry_top <= entry_bottom:
        # A one-indicator level has no natural width.  Represent it as a zone
        # using the approved 0.5% clustering tolerance, while retaining a top
        # strictly below current price for valid LONG entry geometry.
        entry_top = min(current_price * (1 - 0.000001), entry_top * 1.0025)
        entry_bottom *= 0.9975
    entry_mid = (entry_top + entry_bottom) / 2
    sl_level = _find_sl(support_clusters, entry_bottom)
    return {
        "symbol": symbol,
        "current_price": float(current_price),
        "entry_zone_top": entry_top,
        "entry_zone_bottom": entry_bottom,
        "entry_mid": entry_mid,
        "sl_level": sl_level,
        "stop_dist_pct": max(0.0, (entry_mid - sl_level) / entry_mid * 100) if entry_mid else 0.0,
        "resistances": _top_resistances(resistance_clusters, current_price),
        "confluence_score": float(entry_zone.get("score", 0.0)),
        "gap_to_zone_pct": max(0.0, (current_price - entry_top) / current_price * 100),
    }


def dead_cat_check(symbol: str, entry_zone_bottom: float) -> dict:
    """Check the three required 5m-candle conditions before a manual entry."""
    raw = bybit_api.get_klines(symbol, "5", 5)
    candles = _api_candles(raw)
    if len(candles) < 5:
        logger.warning("dead_cat_insufficient_candles symbol=%s count=%d", symbol, len(candles))
        return {
            "passed": False,
            "rule1_midpoint": {"pass": False, "close": 0.0, "midpoint": 0.0},
            "rule2_volume": {"pass": False, "volume": 0.0, "threshold": 0.0},
            "rule3_higher_low": {"pass": False, "low": 0.0, "prior_low": 0.0},
        }
    candle = candles[-2]
    prior_candles = candles[-5:-2]
    prior_candle = candles[-3]
    midpoint = (candle["high"] + candle["low"]) / 2
    volume_threshold = 0.70 * float(np.mean([item["volume"] for item in prior_candles]))
    rule1 = candle["close"] >= midpoint
    rule2 = candle["volume"] >= volume_threshold
    rule3 = candle["low"] > prior_candle["low"]
    return {
        "passed": rule1 and rule2 and rule3,
        "rule1_midpoint": {"pass": rule1, "close": candle["close"], "midpoint": midpoint},
        "rule2_volume": {"pass": rule2, "volume": candle["volume"], "threshold": volume_threshold},
        "rule3_higher_low": {"pass": rule3, "low": candle["low"], "prior_low": prior_candle["low"]},
    }
