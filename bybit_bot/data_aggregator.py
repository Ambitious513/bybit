"""Data aggregator — abstraction layer between bybit_api.py and research.py.

Assembles normalized capital_flow dict from 4 Bybit endpoints concurrently.
Accumulates OI history in data/oi_history.json for 30-day trend calculation.

Swap data sources (e.g. Coinglass) here without touching research.py.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Optional

from bybit_bot import bybit_api

logger = logging.getLogger("data_aggregator")

_OI_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "data", "oi_history.json")


# ── OI history helpers ────────────────────────────────────────────────────────

def _load_oi_history() -> dict:
    """Load OI history file. Returns {symbol: [{timestamp, openInterest}]}."""
    if not os.path.exists(_OI_HISTORY_PATH):
        return {}
    try:
        with open(_OI_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_oi_history(history: dict) -> None:
    """Persist OI history atomically."""
    tmp = _OI_HISTORY_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(_OI_HISTORY_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f)
        os.replace(tmp, _OI_HISTORY_PATH)
    except OSError as exc:
        logger.error("oi_history_save_error error=%s", exc)


def _append_oi_snapshot(symbol: str, oi_value: float) -> None:
    """Append a new OI snapshot to history. Prune entries older than 35 days."""
    history = _load_oi_history()
    now_iso = datetime.now(UTC).isoformat()
    history.setdefault(symbol, [])
    history[symbol].append({"timestamp": now_iso, "openInterest": oi_value})

    # Prune older than 35 days
    cutoff = datetime.now(UTC) - timedelta(days=35)
    history[symbol] = [
        e for e in history[symbol]
        if datetime.fromisoformat(e["timestamp"]) > cutoff
    ]
    _save_oi_history(history)


def _get_oi_30d_ago(symbol: str) -> Optional[float]:
    """Find OI entry closest to 30 days ago. Returns None if < 30 days of data."""
    history = _load_oi_history()
    entries = history.get(symbol, [])
    if not entries:
        return None

    oldest = datetime.fromisoformat(entries[0]["timestamp"])
    if datetime.now(UTC) - oldest < timedelta(days=29):
        return None  # insufficient history

    target = datetime.now(UTC) - timedelta(days=30)
    closest = min(entries, key=lambda e: abs(
        datetime.fromisoformat(e["timestamp"]) - target
    ))
    return float(closest["openInterest"])


# ── Public API ────────────────────────────────────────────────────────────────

def get_capital_flow(symbol: str) -> Optional[dict]:
    """Assemble normalized capital flow dict from 4 Bybit endpoints.

    Calls concurrently:
      - get_long_short_ratio(symbol)
      - get_top_trader_ratio(symbol)
      - get_funding_rate(symbol)
      - get_open_interest(symbol)

    Derives:
      whale_ratio = buyRatio / sellRatio
      fund_side   = "Bullish" if funding_rate >= 0 else "Bearish"

    Returns normalized dict or None if any required sub-call fails.
    """
    results: dict = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(bybit_api.get_long_short_ratio, symbol): "ls_ratio",
            executor.submit(bybit_api.get_top_trader_ratio, symbol): "top_trader",
            executor.submit(bybit_api.get_funding_rate, symbol):     "funding",
            executor.submit(bybit_api.get_open_interest, symbol):    "oi",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                logger.error("capital_flow_fetch_error key=%s symbol=%s error=%s", key, symbol, exc)
                results[key] = None

    ls    = results.get("ls_ratio")
    top   = results.get("top_trader")
    fund  = results.get("funding")
    oi    = results.get("oi")

    # All 3 primary fields required (oi allowed to be partial)
    if not ls or not fund:
        logger.warning("capital_flow_missing_required_data symbol=%s ls=%s fund=%s",
                       symbol, bool(ls), bool(fund))
        return None

    # Derive whale_ratio (guard against zero division)
    buy_ratio  = float(ls.get("buyRatio", 0.5))
    sell_ratio = float(ls.get("sellRatio", 0.5))
    whale_ratio = round(buy_ratio / sell_ratio, 4) if sell_ratio > 0 else 1.0

    # Derive top_trader_ratio
    if top:
        tt_buy  = float(top.get("buyRatio", 0.5))
        tt_sell = float(top.get("sellRatio", 0.5))
        top_trader_ratio = round(tt_buy / tt_sell, 4) if tt_sell > 0 else 1.0
    else:
        top_trader_ratio = 1.0

    # Derive fund_side from funding rate sign
    funding_rate = float(fund.get("fundingRate", 0))
    fund_side = "Bullish" if funding_rate >= 0 else "Bearish"

    # OI — append snapshot, load 30d history
    oi_current: Optional[float] = None
    oi_30d_ago: Optional[float] = None
    if oi:
        try:
            oi_current = float(oi["openInterest"])
            _append_oi_snapshot(symbol, oi_current)
            oi_30d_ago = _get_oi_30d_ago(symbol)
        except (KeyError, ValueError) as exc:
            logger.warning("oi_parse_error symbol=%s error=%s", symbol, exc)

    return {
        "longShortRatio":        whale_ratio,
        "fundSide":              fund_side,
        "fundingRate":           {"latest": funding_rate},
        "topTraderPositionRate": top_trader_ratio,
        "openInterestHistory": {
            "current":       oi_current,
            "thirtyDaysAgo": oi_30d_ago,
        },
    }
