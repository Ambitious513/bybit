"""Lightweight high-conviction opportunity scanner.

Quick scan is alert-only. It uses deterministic flow thresholds and never
calculates a trade, sends an order, or changes the full research pipeline.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_bot import bybit_api, data_aggregator, telegram
from bybit_bot.config import HARD_CLOSE_UTC_HOUR, MAX_TRADES_PER_SESSION, PERMANENT_SKIP_LIST, SESSION_SKIP_LIST

logger = logging.getLogger("quickscan")

HIGH_CONVICTION_THRESHOLD = {
    "whale_ratio_min": 2.5,
    "fund_side": "Bullish",
    "volume_min_usd": 5_000_000,
    "price_change_min": 8,
    "price_change_max": 40,
}

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_RESEARCH_CACHE_PATH = os.path.join(_DATA_DIR, "research_cache.json")
_ACTIVE_ORDERS_PATH = os.path.join(_DATA_DIR, "active_orders.json")


def _utc_now() -> datetime:
    """Return timezone-aware UTC time used by quick-scan guards and formatting."""
    return datetime.now(UTC)


def _float(value: Any, default: float = 0.0) -> float:
    """Convert a market-data field to a float without propagating bad input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent(value: Any) -> float:
    """Normalize Bybit fractional percent data to conventional percentage points."""
    result = _float(value)
    return result * 100 if abs(result) <= 1 else result


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp as UTC, returning None for unavailable cache metadata."""
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _load_orders() -> list[dict] | None:
    """Load active orders or return None when state is malformed and unsafe to trust."""
    try:
        with open(_ACTIVE_ORDERS_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("expected array")
        return payload
    except FileNotFoundError:
        return []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("quickscan_active_orders_unavailable error=%s", exc)
        return None


def should_run_quickscan() -> bool:
    """Return whether timing, session count, and hard-close guards permit a scan."""
    now = _utc_now()
    try:
        with open(_RESEARCH_CACHE_PATH, "r", encoding="utf-8") as handle:
            research = json.load(handle)
        last_research = _parse_timestamp(research.get("timestamp") if isinstance(research, dict) else None)
        if last_research and now - last_research < timedelta(minutes=20):
            logger.info("quickscan_skipped_recent_research")
            return False
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("quickscan_research_cache_unavailable error=%s", exc)

    orders = _load_orders()
    if orders is None:
        return False
    if sum(1 for order in orders if isinstance(order, dict) and order.get("status") == "FILLED") >= MAX_TRADES_PER_SESSION:
        logger.info("quickscan_skipped_max_trades")
        return False
    if now.hour >= HARD_CLOSE_UTC_HOUR:
        logger.info("quickscan_skipped_hard_close")
        return False
    return True


def _is_skipped(symbol: str) -> bool:
    """Apply permanent and in-memory session exclusions to a perpetual symbol."""
    normalized = symbol.upper()
    base = normalized.removesuffix("USDT")
    permanent = {item.upper().removesuffix("USDT") for item in PERMANENT_SKIP_LIST}
    session = {item.upper() for item in SESSION_SKIP_LIST}
    return base in permanent or normalized in session or base in session


def _next_research_time(now: datetime) -> str:
    """Return the next four-hour research boundary in UTC for the alert text."""
    next_hour = (now.hour // 4 + 1) * 4
    boundary = now.replace(minute=0, second=0, microsecond=0)
    if next_hour >= 24:
        boundary = boundary.replace(hour=0) + timedelta(days=1)
    else:
        boundary = boundary.replace(hour=next_hour)
    return boundary.strftime("%H:%M")


def format_quickscan_alert(coin: dict, flow: dict) -> str:
    """Format the approved early-warning HTML message for a qualified coin."""
    now = _utc_now()
    symbol = str(coin["symbol"])
    price = _float(coin.get("price"))
    change = _percent(coin.get("price24hPcnt"))
    volume_millions = _float(coin.get("volume", coin.get("turnover24h"))) / 1_000_000
    whale_ratio = _float(flow.get("longShortRatio"))
    return (
        f"⚡ <b>QUICK SCAN ALERT — {now.strftime('%H:%M')} UTC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>HIGH CONVICTION DETECTED</b>\n\n"
        f"<b>{symbol}</b>\n"
        f"  Price:  ${price:g}  ({change:+.1f}% 24H)\n"
        f"  Whale:  {whale_ratio:.1f}:1 NET LONG\n"
        "  Fund:   BULLISH\n"
        f"  Volume: ${volume_millions:.1f}M ✅\n\n"
        "→ Full research NOT yet run for this coin\n"
        "→ S/R zones NOT calculated yet\n"
        "→ This is an <b>EARLY WARNING only</b>\n\n"
        f"ACTION: tap to copy → <code>/deepdive {symbol.removesuffix('USDT')}</code>\n"
        f"        OR wait for next research at {_next_research_time(now)} UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Do NOT trade without full S/R data"
    )


def run_quickscan() -> list[dict]:
    """Run the bounded six-call high-conviction scanner and send early warnings."""
    if not should_run_quickscan():
        return []
    candidates: list[dict] = []
    for ticker in bybit_api.get_all_linear_tickers():
        symbol = str(ticker.get("symbol", ""))
        change = _percent(ticker.get("price24hPcnt"))
        if (
            _float(ticker.get("turnover24h")) < HIGH_CONVICTION_THRESHOLD["volume_min_usd"]
            or not (HIGH_CONVICTION_THRESHOLD["price_change_min"] < change < HIGH_CONVICTION_THRESHOLD["price_change_max"])
            or _is_skipped(symbol)
        ):
            continue
        candidates.append({
            "symbol": symbol, "price": _float(ticker.get("price")), "price24hPcnt": change,
            "volume": _float(ticker.get("turnover24h")),
        })
    top_candidates = sorted(candidates, key=lambda item: item["price24hPcnt"], reverse=True)[:5]
    hits: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(data_aggregator.get_capital_flow, coin["symbol"]): coin for coin in top_candidates}
        for future in as_completed(futures):
            coin = futures[future]
            try:
                flow = future.result()
            except Exception as exc:
                logger.error("quickscan_flow_error symbol=%s error=%s", coin["symbol"], exc)
                continue
            if not flow:
                continue
            whale_ratio = _float(flow.get("longShortRatio"))
            fund_side = str(flow.get("fundSide", "UNKNOWN"))
            if (
                whale_ratio >= HIGH_CONVICTION_THRESHOLD["whale_ratio_min"]
                and fund_side == HIGH_CONVICTION_THRESHOLD["fund_side"]
            ):
                hit = {**coin, "whale_ratio": whale_ratio, "fund_side": fund_side}
                telegram.send_message(format_quickscan_alert(hit, flow))
                hits.append(hit)
    return sorted(hits, key=lambda item: item["price24hPcnt"], reverse=True)


def classify_btc_regime_quick() -> str:
    """Classify BTC using only its read-only capital-flow primary regime inputs."""
    from bybit_bot.config import BTC_BEAR_WHALE_MAX, BTC_BULL_WHALE_MIN

    flow = data_aggregator.get_capital_flow("BTCUSDT")
    if not flow:
        logger.warning("quick_btc_flow_unavailable")
        return "CHOPPY"
    whale_ratio = _float(flow.get("longShortRatio"))
    fund_side = flow.get("fundSide")
    if whale_ratio >= BTC_BULL_WHALE_MIN and fund_side == "Bullish":
        return "BULLISH"
    if whale_ratio <= BTC_BEAR_WHALE_MAX and fund_side == "Bearish":
        return "BEARISH"
    return "CHOPPY"
