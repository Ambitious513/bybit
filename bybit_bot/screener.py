"""Coin-universe filters for the research pipeline.

This module uses only read-only Bybit market-data functions and never places
orders.  Percentage changes returned by Bybit are normalized to percentage
points here (for example ``0.085`` becomes ``8.5``).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bybit_bot import bybit_api
from bybit_bot.config import PERMANENT_SKIP_LIST, SESSION_SKIP_LIST, WATCHLIST_STANDING

logger = logging.getLogger("screener")


def _as_float(value: Any, default: float = 0.0) -> float:
    """Convert a market-data value to float without propagating bad input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent(value: Any) -> float:
    """Normalize Bybit's fractional percentage field to percentage points."""
    change = _as_float(value)
    return change * 100 if abs(change) <= 1 else change


def _is_skipped(symbol: str) -> bool:
    """Return whether a symbol is permanently or session excluded."""
    normalized = symbol.upper()
    base = normalized[:-4] if normalized.endswith("USDT") else normalized
    permanent = {item.upper().removesuffix("USDT") for item in PERMANENT_SKIP_LIST}
    session = {item.upper() for item in SESSION_SKIP_LIST}
    return base in permanent or normalized in session or base in session


def _screened_tickers(tag: str, min_volume_usd: float, limit: int) -> list[dict]:
    """Return ticker candidates matching the gainers or losers threshold."""
    candidates: list[dict] = []
    for ticker in bybit_api.get_all_linear_tickers():
        symbol = str(ticker.get("symbol", ""))
        change = _percent(ticker.get("price24hPcnt"))
        if (
            _as_float(ticker.get("turnover24h")) < min_volume_usd
            or _is_skipped(symbol)
        ):
            continue
        if tag == "GAINER" and not (change > 8.0 and change < 50.0):
            continue
        if tag == "LOSER" and not change < -10.0:
            continue
        candidates.append({
            "symbol": symbol,
            "price": ticker.get("price"),
            "price24hPcnt": change,
            "turnover24h": ticker.get("turnover24h"),
            "tag": tag,
        })
    candidates.sort(key=lambda item: item["price24hPcnt"], reverse=tag == "GAINER")
    return candidates[:limit]


def get_gainers(min_volume_usd: float = 1_000_000, limit: int = 10) -> list[dict]:
    """Return liquid, non-excluded perpetuals up 8% to 50% in 24 hours.

    If there are no qualifying gainers, standing-watchlist instruments are
    returned as the screener fallback.
    """
    gainers = _screened_tickers("GAINER", min_volume_usd, limit)
    if not gainers:
        logger.warning("screener_empty")
        return get_standing_watchlist()
    return gainers


def get_losers(min_volume_usd: float = 1_000_000, limit: int = 10) -> list[dict]:
    """Return liquid, non-excluded perpetuals down more than 10% in 24 hours.

    The caller is responsible for invoking this only in a BULLISH BTC regime,
    where loser reversal plays are permitted by the strategy.
    """
    return _screened_tickers("LOSER", min_volume_usd, limit)


def get_standing_watchlist() -> list[dict]:
    """Fetch standing-watchlist tickers concurrently, skipping failed symbols."""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(WATCHLIST_STANDING) or 1) as executor:
        futures = {
            executor.submit(bybit_api.get_ticker, symbol): (symbol, details)
            for symbol, details in WATCHLIST_STANDING.items()
        }
        for future in as_completed(futures):
            symbol, details = futures[future]
            try:
                ticker = future.result()
            except Exception as exc:
                logger.warning("watchlist_ticker_error symbol=%s error=%s", symbol, exc)
                continue
            if not ticker:
                logger.warning("watchlist_ticker_missing symbol=%s", symbol)
                continue
            results.append({
                "symbol": symbol,
                "price": ticker.get("price"),
                "price24hPcnt": _percent(ticker.get("price24hPcnt")),
                "turnover24h": ticker.get("turnover24h"),
                "tag": "WATCHLIST",
                "qualify_if": details["qualify_if"],
                "skip_if": details["skip_if"],
                "edge": details["edge"],
                "note": details["note"],
            })
    return sorted(results, key=lambda item: item["symbol"])
