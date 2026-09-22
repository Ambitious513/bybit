"""Six-step read-only research pipeline for the Bybit Sniper Bot."""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import requests

from bybit_bot import bybit_api, data_aggregator, openrouter, screener, sr_calculator, telegram
from bybit_bot.config import (
    BTC_BEAR_WHALE_MAX,
    BTC_BULL_WHALE_MIN,
    BTC_STRONG_MIN,
    PERMANENT_SKIP_LIST,
    SESSION_SKIP_LIST,
    TRADFI_MIN_WHALE_RATIO,
    TRADFI_PERPS,
)

logger = logging.getLogger("research")

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "research_cache.json")
_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_NEWS_SYSTEM_PROMPT = (
    'You are a crypto news analyst. Return JSON only. Format: '
    '{"sentiment":"BULLISH|BEARISH|NEUTRAL","risk_events":["event1"],'
    '"unlock_today":false,"exploit_today":false}'
)

# This calendar is intentionally empty until dated events are approved and added
# by the human operator.  Tests and operations may populate either ISO-date or
# ``date`` keys; event keywords below are the protected halt mechanism.
KNOWN_EVENTS: dict[object, list[str]] = {}
EXTREME_EVENT_KEYWORDS = ["FOMC", "CPI", "PPI", "NFP", "SEC", "DERIBIT QUARTERLY"]


def _float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, returning a deterministic fallback on error."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent(value: Any) -> float:
    """Normalize Bybit fractional percentage changes to percentage points."""
    result = _float(value)
    return result * 100 if abs(result) <= 1 else result


def _funding_rate(flow: dict | None) -> float:
    """Extract the normalized current funding rate from capital-flow data."""
    if not flow:
        return 0.0
    funding = flow.get("fundingRate", 0.0)
    return _float(funding.get("latest") if isinstance(funding, dict) else funding)


def _safe_future(future: Any, name: str, default: Any) -> Any:
    """Resolve a future while logging unexpected dependency failures."""
    try:
        return future.result()
    except Exception as exc:
        logger.error("research_fetch_error source=%s error=%s", name, exc)
        return default


def _fetch_fear_greed() -> dict:
    """Fetch the Fear & Greed index or return its defined no-data fallback."""
    try:
        response = requests.get(_FNG_URL, timeout=10)
        response.raise_for_status()
        item = response.json()["data"][0]
        return {"value": int(item["value"]), "classification": str(item["value_classification"])}
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("fear_greed_unavailable error=%s", exc)
        return {"value": None, "classification": "UNKNOWN"}


def _btc_news_prompt(ticker: dict, flow: dict | None, fear_greed: dict) -> str:
    """Build the constrained BTC-news classification prompt."""
    return (
        "Analyze Bitcoin sentiment from these signals: BTC "
        f"{_percent(ticker.get('price24hPcnt'))}% 24H, whale ratio "
        f"{_float((flow or {}).get('longShortRatio'), 1.0)}:1, fear/greed "
        f"{fear_greed.get('value')}/100. Any obvious risk events? Return JSON."
    )


def _fetch_news(symbol: str, price_change: Any) -> dict:
    """Return LLM news classification, falling back safely to neutral."""
    prompt = f"Coin: {symbol}. Recent context: {symbol} {_percent(price_change)}% today."
    try:
        parsed = openrouter.parse_json_response(
            openrouter.complete(prompt, system_prompt=_NEWS_SYSTEM_PROMPT)
        )
    except Exception as exc:
        logger.error("coin_news_error symbol=%s error=%s", symbol, exc)
        parsed = None
    if not isinstance(parsed, dict):
        return {"sentiment": "NEUTRAL", "risk_events": [], "unlock_today": False, "exploit_today": False}
    return {
        "sentiment": str(parsed.get("sentiment", "NEUTRAL")).upper(),
        "risk_events": parsed.get("risk_events", []),
        "unlock_today": bool(parsed.get("unlock_today", False)),
        "exploit_today": bool(parsed.get("exploit_today", False)),
    }


def classify_btc_regime(flow: dict | None, volatility_proxy: dict, fear_greed: dict) -> dict:
    """Classify BTC deterministically from capital flow and market conditions."""
    flow = flow or {}
    whale_ratio = _float(flow.get("longShortRatio"), 1.0)
    fund_side = str(flow.get("fundSide", "UNKNOWN"))
    funding_rate = _funding_rate(flow)
    funding_magnitude = _float(volatility_proxy.get("funding_magnitude"), abs(funding_rate))

    if whale_ratio >= BTC_BULL_WHALE_MIN and fund_side == "Bullish":
        regime = "BULLISH"
    elif whale_ratio <= BTC_BEAR_WHALE_MAX and fund_side == "Bearish":
        regime = "BEARISH"
    else:
        regime = "CHOPPY"

    sub_type = "NORMAL"
    if regime == "BULLISH":
        if whale_ratio >= BTC_STRONG_MIN:
            sub_type = "STRONG"
        oi_history = flow.get("openInterestHistory") or {}
        current_oi = _float(oi_history.get("current"))
        prior_oi = _float(oi_history.get("thirtyDaysAgo"), current_oi)
        oi_delta = (current_oi - prior_oi) / max(current_oi, 1)
        if oi_delta < -0.02:
            sub_type = "PULLBACK"

    warnings: list[str] = []
    if funding_magnitude > 0.0005:
        warnings.append("CROWDED — funding elevated")
    if _float(volatility_proxy.get("price_range_24h_pct")) > 8:
        warnings.append("HIGH_VOLATILITY — tighten stops")
    if fear_greed.get("value") is not None and _float(fear_greed.get("value")) >= 80:
        warnings.append("EXTREME_GREED — reduce size")

    direction = {"BULLISH": "LONG", "BEARISH": "SHORT", "CHOPPY": "CHOP_ONLY"}[regime]
    return {
        "regime": regime,
        "sub_type": sub_type,
        "direction": direction,
        "whale_ratio": whale_ratio,
        "fund_side": fund_side,
        "funding_rate": funding_rate,
        "fear_greed": fear_greed,
        "warnings": warnings,
        "btc_price": _float(volatility_proxy.get("btc_price")),
        "long_ok": regime == "BULLISH",
    }


def _is_skipped(symbol: str) -> bool:
    """Apply permanent and session exclusions to a coin symbol."""
    normalized = symbol.upper()
    base = normalized.removesuffix("USDT")
    permanent = {item.upper().removesuffix("USDT") for item in PERMANENT_SKIP_LIST}
    session = {item.upper() for item in SESSION_SKIP_LIST}
    return base in permanent or normalized in session or base in session


def _watchlist_condition_passes(condition: str, coin: dict, flow: dict) -> bool:
    """Evaluate approved watchlist conditions without using dynamic ``eval``."""
    values: dict[str, Any] = {
        "whale_ratio": _float(flow.get("longShortRatio")),
        "fund_side": str(flow.get("fundSide", "")),
        "top_trader_ratio": _float(flow.get("topTraderPositionRate")),
        "price_change_24h": _percent(coin.get("price24hPcnt")),
        "funding": _funding_rate(flow),
    }
    clauses = re.split(r"\s+AND\s+", condition, flags=re.IGNORECASE)
    matcher = re.compile(r"^\s*([a-z_]+)\s*(>=|<=|==|>|<)\s*([A-Za-z0-9.\-]+)\s*$", re.I)
    for clause in clauses:
        matched = matcher.match(clause)
        if not matched:
            logger.warning("watchlist_condition_unparsed condition=%s", condition)
            return False
        field, operator, expected = matched.groups()
        actual = values.get(field.lower())
        if actual is None:
            return False
        if isinstance(actual, str):
            passed = operator == "==" and actual.lower() == expected.lower()
        else:
            target = _float(expected)
            passed = {
                ">": actual > target, ">=": actual >= target,
                "<": actual < target, "<=": actual <= target,
                "==": actual == target,
            }[operator]
        if not passed:
            return False
    return True


def _flow_for_coin(coin: dict) -> dict | None:
    """Fetch flow unless a TradFi scan has already supplied its key fields."""
    if coin.get("tag") == "TRADFI":
        return {
            "longShortRatio": coin.get("whale_ratio", 0.0),
            "fundSide": coin.get("fund_side", "UNKNOWN"),
            "fundingRate": {"latest": 0.0},
            "topTraderPositionRate": 0.0,
            "openInterestHistory": {},
        }
    return data_aggregator.get_capital_flow(str(coin.get("symbol", "")))


def qualify_coins(gainers: list[dict], losers: list[dict], watchlist: list[dict], tradfi: list[dict], regime: dict) -> list[dict]:
    """Attach flow/news and return coins qualified under the approved rules."""
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for coin in [*gainers, *losers, *watchlist, *tradfi]:
        symbol = str(coin.get("symbol", ""))
        tag = str(coin.get("tag", ""))
        supplied_news = coin.get("news") if isinstance(coin.get("news"), dict) else {}
        identity = (symbol, tag)
        if (
            not symbol
            or identity in seen
            or _is_skipped(symbol)
            or supplied_news.get("unlock_today")
            or supplied_news.get("exploit_today")
        ):
            continue
        seen.add(identity)
        candidates.append(coin)
    flows: dict[int, dict | None] = {}
    with ThreadPoolExecutor(max_workers=min(max(len(candidates), 1), 12)) as executor:
        futures = {executor.submit(_flow_for_coin, coin): index for index, coin in enumerate(candidates)}
        for future in as_completed(futures):
            index = futures[future]
            flows[index] = _safe_future(future, "coin_flow", None)

    prelim: list[dict] = []
    for index, coin in enumerate(candidates):
        symbol = str(coin.get("symbol", ""))
        tag = coin.get("tag")
        flow = flows.get(index)
        is_qualified = False
        if tag in {"GAINER", "LOSER"}:
            is_qualified = regime.get("regime") == "BULLISH"
        elif tag == "WATCHLIST" and flow:
            is_qualified = _watchlist_condition_passes(str(coin.get("qualify_if", "")), coin, flow)
        elif tag == "TRADFI" and flow:
            is_qualified = (
                _float(flow.get("longShortRatio")) >= TRADFI_MIN_WHALE_RATIO
                and flow.get("fundSide") == "Bullish"
            )
        if is_qualified:
            enriched = dict(coin)
            enriched["flow"] = flow or {}
            prelim.append(enriched)

    qualified: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(max(len(prelim), 1), 8)) as executor:
        futures = {
            executor.submit(_fetch_news, coin["symbol"], coin.get("price24hPcnt", 0)): coin
            for coin in prelim
        }
        for future in as_completed(futures):
            coin = futures[future]
            news = _safe_future(
                future,
                "coin_news",
                {"sentiment": "NEUTRAL", "risk_events": [], "unlock_today": False, "exploit_today": False},
            )
            if news["unlock_today"] or news["exploit_today"]:
                logger.warning("coin_instant_disqualify symbol=%s", coin["symbol"])
                continue
            coin["news"] = news
            qualified.append(coin)
    return sorted(qualified, key=lambda item: (_float(item.get("price24hPcnt")), item["symbol"]), reverse=True)


def scan_tradfi_perps() -> list[dict]:
    """Return TradFi perpetuals whose capital flow meets the approved filter."""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(TRADFI_PERPS) * 2 or 1) as executor:
        futures = {}
        for symbol in TRADFI_PERPS:
            futures[executor.submit(data_aggregator.get_capital_flow, symbol)] = (symbol, "flow")
            futures[executor.submit(bybit_api.get_ticker, symbol)] = (symbol, "ticker")
        collected: dict[str, dict[str, Any]] = {symbol: {} for symbol in TRADFI_PERPS}
        for future in as_completed(futures):
            symbol, kind = futures[future]
            collected[symbol][kind] = _safe_future(future, f"tradfi_{kind}", None)
    for symbol, data in collected.items():
        flow, ticker = data.get("flow"), data.get("ticker")
        if not flow or not ticker:
            continue
        whale_ratio = _float(flow.get("longShortRatio"))
        fund_side = str(flow.get("fundSide", "UNKNOWN"))
        if whale_ratio >= TRADFI_MIN_WHALE_RATIO and fund_side == "Bullish":
            results.append({
                "symbol": symbol,
                "price": ticker.get("price"),
                "price24hPcnt": _percent(ticker.get("price24hPcnt")),
                "whale_ratio": whale_ratio,
                "fund_side": fund_side,
                "tag": "TRADFI",
            })
    return results


def _events_today() -> list[str]:
    """Return configured events for the current UTC date."""
    today = datetime.utcnow().date()
    return list(KNOWN_EVENTS.get(today, KNOWN_EVENTS.get(today.isoformat(), [])))


def _is_extreme_event(events: list[str]) -> bool:
    """Return whether any current event requires an immediate research halt."""
    return any(keyword in event.upper() for event in events for keyword in EXTREME_EVENT_KEYWORDS)


def _save_research(result: dict) -> None:
    """Persist research output atomically so readers never see partial JSON."""
    temporary_path = f"{_CACHE_PATH}.tmp"
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        os.replace(temporary_path, _CACHE_PATH)
    except OSError as exc:
        logger.error("research_cache_save_error error=%s", exc)


def _format_research_card(result: dict) -> str:
    """Build the non-monospace Telegram HTML research summary."""
    regime = result["regime"]
    fear_greed = regime["fear_greed"]
    lines = [
        "<b>🔎 Bybit Sniper Research</b>",
        f"<b>{regime['regime']} · {regime['sub_type']} · {regime['direction']}</b>",
        f"BTC ${regime['btc_price']:,.2f} | Whale {regime['whale_ratio']:.2f}:1 | {regime['fund_side']}",
        f"Fear &amp; Greed: {fear_greed['value'] if fear_greed['value'] is not None else 'UNKNOWN'} ({fear_greed['classification']})",
    ]
    if regime["warnings"]:
        lines.append("⚠️ " + " | ".join(regime["warnings"]))
    if result["events_today"]:
        lines.append("📅 Events: " + " | ".join(result["events_today"]))
    coins = result["qualified_coins"][:5]
    lines.append("<b>Qualified coins</b>:" if coins else "<b>Qualified coins</b>: none")
    for coin in coins:
        lines.append(f"• <b>{coin['symbol']}</b> ({coin['tag']}) {coin.get('price24hPcnt', 0):+.2f}%")
    return "\n".join(lines)


def run_research() -> dict:
    """Run the complete research pipeline, cache results, and notify Telegram."""
    # Wave one contains independent I/O. Losers are deliberately deferred until
    # after deterministic regime classification because they are BULLISH-only.
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            "btc_ticker": executor.submit(bybit_api.get_ticker, "BTCUSDT"),
            "btc_flow": executor.submit(data_aggregator.get_capital_flow, "BTCUSDT"),
            "fear_greed": executor.submit(_fetch_fear_greed),
            "gainers": executor.submit(screener.get_gainers),
            "watchlist": executor.submit(screener.get_standing_watchlist),
            "tradfi": executor.submit(scan_tradfi_perps),
        }
        fetched = {name: _safe_future(future, name, [] if name in {"gainers", "watchlist", "tradfi"} else None)
                   for name, future in futures.items()}

    btc_ticker = fetched["btc_ticker"] or {}
    btc_flow = fetched["btc_flow"]
    volatility_proxy = {
        "price_range_24h_pct": (
            (_float(btc_ticker.get("high24h")) - _float(btc_ticker.get("low24h")))
            / max(_float(btc_ticker.get("price")), 1) * 100
        ),
        "funding_magnitude": abs(_funding_rate(btc_flow)),
        "crowded": abs(_funding_rate(btc_flow)) > 0.0005,
        "btc_price": _float(btc_ticker.get("price")),
    }
    fear_greed = fetched["fear_greed"] or {"value": None, "classification": "UNKNOWN"}
    regime = classify_btc_regime(btc_flow, volatility_proxy, fear_greed)
    # BTC support is presentation context for downstream execution cards.  It
    # is derived solely by the deterministic S/R module, never by an LLM.
    try:
        btc_sr = sr_calculator.get_sr_levels("BTCUSDT", _float(btc_ticker.get("price")))
        regime["btc_support"] = _float(btc_sr.get("entry_zone_bottom"))
    except Exception as exc:
        logger.error("btc_support_sr_unavailable error=%s", exc)
        regime["btc_support"] = 0.0
    events = _events_today()
    timestamp = datetime.now(UTC).isoformat()

    if _is_extreme_event(events):
        result = {
            "timestamp": timestamp, "regime": regime, "qualified_coins": [],
            "halt_research": True, "events_today": events,
        }
        _save_research(result)
        telegram.send_message("⚠️ <b>Research halted</b> — extreme event today: " + " | ".join(events))
        logger.warning("research_halted_extreme_event events=%s", events)
        return result

    losers = screener.get_losers() if regime["regime"] == "BULLISH" else []
    # BTC-news output is included in the persisted BTC flow without participating
    # in deterministic regime or qualification calculations.
    btc_news = _fetch_news("BTC", _percent(btc_ticker.get("price24hPcnt")))
    if btc_flow is not None:
        btc_flow = dict(btc_flow)
        btc_flow["news"] = btc_news
    qualified = qualify_coins(fetched["gainers"], losers, fetched["watchlist"], fetched["tradfi"], regime)
    result = {
        "timestamp": timestamp,
        "regime": regime,
        "qualified_coins": qualified,
        "halt_research": False,
        "events_today": events,
    }
    _save_research(result)
    telegram.send_message(_format_research_card(result))

    try:
        from bybit_bot import planning
        planning.run_planning(result)
    except ImportError:
        logger.info("planning_not_available — research chain deferred to TASK-018")
    except Exception as exc:
        logger.error("planning_chain_error error=%s", exc)
        telegram.send_message("⚠️ <b>Planning chain failed</b> — research was saved successfully")
    return result
