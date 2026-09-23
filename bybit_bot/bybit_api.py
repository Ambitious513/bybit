"""Bybit V5 REST API wrapper — READ ONLY market data.
No authentication required for any endpoint used here.
No order placement. Ever.

Rate limiting: 0.5s sleep between sequential calls.
Retry: 3x on 429 (2s backoff), 2x on 500 (1s backoff).
Returns None on all other errors — never raises to caller.
"""

import logging
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from bybit_bot.config import BYBIT_BASE_URL

logger = logging.getLogger("bybit_api")

# ── HTTP Session with explicit pool cap (TASK-026 R5) ─────────────────────────
# Prevents urllib3 connection-pool overflow warnings when quickscan fires
# concurrent API calls via ThreadPoolExecutor.
_ADAPTER = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://",  _ADAPTER)

# ── Internal helpers ──────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> Optional[dict]:
    """GET request with retry logic. Returns parsed JSON result or None."""
    url = f"{BYBIT_BASE_URL}{endpoint}"
    last_exc: Optional[Exception] = None

    for attempt in range(4):
        try:
            resp = _SESSION.get(url, params=params, timeout=10)

            if resp.status_code == 429:
                wait = 2 * (attempt + 1)
                logger.warning("rate_limited endpoint=%s attempt=%d wait=%ds", endpoint, attempt, wait)
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                if attempt < 2:
                    logger.warning("server_error status=%d endpoint=%s", resp.status_code, endpoint)
                    time.sleep(1)
                    continue
                logger.error("server_error_max_retries endpoint=%s", endpoint)
                return None

            if resp.status_code != 200:
                logger.error("unexpected_status status=%d endpoint=%s", resp.status_code, endpoint)
                return None

            data = resp.json()
            if data.get("retCode") != 0:
                logger.error("bybit_error code=%s msg=%s endpoint=%s",
                             data.get("retCode"), data.get("retMsg"), endpoint)
                return None

            return data

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            logger.error("request_error endpoint=%s error=%s", endpoint, exc)
            if attempt < 3:
                time.sleep(1)

    if last_exc:
        logger.error("request_failed_all_retries endpoint=%s error=%s", endpoint, last_exc)
    return None


def _sleep() -> None:
    """Rate-limit guard between sequential calls."""
    time.sleep(0.5)


# ── Public API ────────────────────────────────────────────────────────────────

def get_ticker(symbol: str) -> Optional[dict]:
    """Fetch ticker for a single linear perpetual symbol.

    Returns:
        {price, price24hPcnt, turnover24h, high24h, low24h} or None.
    """
    data = _get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    if not data:
        return None
    try:
        item = data["result"]["list"][0]
        return {
            "price":        item["lastPrice"],
            "price24hPcnt": item["price24hPcnt"],
            "turnover24h":  item["turnover24h"],
            "high24h":      item["highPrice24h"],
            "low24h":       item["lowPrice24h"],
        }
    except (KeyError, IndexError) as exc:
        logger.error("get_ticker_parse_error symbol=%s error=%s", symbol, exc)
        return None


def get_all_linear_tickers() -> list[dict]:
    """Fetch all USDT linear perpetual tickers.

    Returns:
        List of {symbol, price, price24hPcnt, turnover24h, high24h, low24h}.
        Empty list on failure.
    """
    data = _get("/v5/market/tickers", {"category": "linear"})
    if not data:
        return []
    results = []
    for item in data.get("result", {}).get("list", []):
        try:
            results.append({
                "symbol":       item["symbol"],
                "price":        item["lastPrice"],
                "price24hPcnt": item["price24hPcnt"],
                "turnover24h":  item["turnover24h"],
                "high24h":      item["highPrice24h"],
                "low24h":       item["lowPrice24h"],
            })
        except KeyError:
            continue
    return results


def get_klines(symbol: str, interval: str, limit: int) -> list:
    """Fetch OHLCV candles for a symbol.

    Args:
        symbol:   e.g. "BTCUSDT"
        interval: "1", "5", "15", "60", "240", "D"
        limit:    number of candles (max 200)

    Returns:
        List of [timestamp, open, high, low, close, volume, turnover]
        ordered oldest-first. Empty list on failure.
    """
    data = _get("/v5/market/kline", {
        "category": "linear",
        "symbol":   symbol,
        "interval": interval,
        "limit":    limit,
    })
    if not data:
        return []
    raw = data.get("result", {}).get("list", [])
    # Bybit returns newest-first — reverse to oldest-first
    return list(reversed(raw))


def get_long_short_ratio(symbol: str, period: str = "1h") -> Optional[dict]:
    """Fetch long/short account ratio for a symbol.

    Returns:
        {buyRatio, sellRatio, timestamp} or None.
    """
    _sleep()
    data = _get("/v5/market/account-ratio", {
        "category": "linear",
        "symbol":   symbol,
        "period":   period,
        "limit":    1,
    })
    if not data:
        return None
    try:
        item = data["result"]["list"][0]
        return {
            "buyRatio":  item["buyRatio"],
            "sellRatio": item["sellRatio"],
            "timestamp": item["timestamp"],
        }
    except (KeyError, IndexError) as exc:
        logger.error("get_long_short_ratio_parse_error symbol=%s error=%s", symbol, exc)
        return None


def get_top_trader_ratio(symbol: str, period: str = "1h") -> Optional[dict]:
    """Fetch top trader position ratio for a symbol.

    Returns:
        {buyRatio, sellRatio} or None.
    """
    _sleep()
    data = _get("/v5/market/account-ratio", {
        "category": "linear",
        "symbol":   symbol,
        "period":   period,
        "limit":    1,
        "type":     "topTraderAccount",
    })
    if not data:
        return None
    try:
        item = data["result"]["list"][0]
        return {
            "buyRatio":  item["buyRatio"],
            "sellRatio": item["sellRatio"],
        }
    except (KeyError, IndexError) as exc:
        logger.error("get_top_trader_ratio_parse_error symbol=%s error=%s", symbol, exc)
        return None


def get_funding_rate(symbol: str) -> Optional[dict]:
    """Fetch the most recent funding rate for a symbol.

    Returns:
        {fundingRate, fundingRateTimestamp} or None.
    """
    _sleep()
    data = _get("/v5/market/funding/history", {
        "category": "linear",
        "symbol":   symbol,
        "limit":    1,
    })
    if not data:
        return None
    try:
        item = data["result"]["list"][0]
        return {
            "fundingRate":          item["fundingRate"],
            "fundingRateTimestamp": item["fundingRateTimestamp"],
        }
    except (KeyError, IndexError) as exc:
        logger.error("get_funding_rate_parse_error symbol=%s error=%s", symbol, exc)
        return None


def get_open_interest(symbol: str) -> Optional[dict]:
    """Fetch the most recent open interest snapshot for a symbol.

    Returns:
        {openInterest, timestamp} or None.
    """
    _sleep()
    data = _get("/v5/market/open-interest", {
        "category":     "linear",
        "symbol":       symbol,
        "intervalTime": "1h",
        "limit":        1,
    })
    if not data:
        return None
    try:
        item = data["result"]["list"][0]
        return {
            "openInterest": item["openInterest"],
            "timestamp":    item["timestamp"],
        }
    except (KeyError, IndexError) as exc:
        logger.error("get_open_interest_parse_error symbol=%s error=%s", symbol, exc)
        return None
