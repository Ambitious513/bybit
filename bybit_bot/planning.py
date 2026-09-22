"""Deterministic scoring and top-three setup selection."""

import json
import logging
import os
from typing import Any

from bybit_bot import data_aggregator, openrouter, sr_calculator, telegram
from bybit_bot.config import PERMANENT_SKIP_LIST

logger = logging.getLogger("planning")

_RESEARCH_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "research_cache.json")
_NEWS_SYSTEM_PROMPT = (
    'You are a crypto news analyst. Return JSON only. Format: '
    '{"sentiment":"BULLISH|BEARISH|NEUTRAL","risk_events":[],"unlock_today":false,"exploit_today":false}'
)


def _float(value: Any, default: float = 0.0) -> float:
    """Convert values from data sources to float without raising to callers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _neutral_news() -> dict:
    """Return the required no-LLM news fallback."""
    return {"sentiment": "NEUTRAL", "risk_events": [], "unlock_today": False, "exploit_today": False}


def score_coin(symbol: str, flow: dict, news_result: dict, tag: str) -> int:
    """Apply the immutable deterministic conviction-scoring system exactly."""
    flow = flow or {}
    score = 0
    whale = _float(flow.get("longShortRatio"))
    if whale >= 9.0:
        score += 60
    elif whale >= 5.0:
        score += 50
    elif whale >= 3.0:
        score += 40
    elif whale >= 2.0:
        score += 30
    elif whale >= 1.5:
        score += 20
    else:
        score += 5

    fund_side = flow.get("fundSide")
    if fund_side == "Bullish":
        score += 20
    if fund_side == "Bearish":
        score -= 30

    top_trader = _float(flow.get("topTraderPositionRate"))
    if top_trader >= 2.0:
        score += 15
    elif top_trader >= 1.5:
        score += 8

    funding_data = flow.get("fundingRate", {})
    funding = abs(_float(funding_data.get("latest") if isinstance(funding_data, dict) else funding_data))
    if funding <= 0.0001:
        score += 10
    if funding >= 0.0005:
        score -= 15

    oi_history = flow.get("openInterestHistory") or {}
    oi_now = _float(oi_history.get("current"))
    oi_30d = _float(oi_history.get("thirtyDaysAgo"))
    if oi_30d:
        oi_growth = (oi_now - oi_30d) / oi_30d
        if oi_growth > 0.05:
            score += 10
        if oi_growth < -0.10:
            score -= 20

    if news_result:
        sentiment = str(news_result.get("sentiment", "")).upper()
        if sentiment == "BULLISH":
            score += 10
        if sentiment == "BEARISH":
            score -= 20
        if news_result.get("unlock_today"):
            score = -999
        if news_result.get("exploit_today"):
            score = -999

    base_symbol = symbol.upper().removesuffix("USDT")
    if base_symbol in {item.upper().removesuffix("USDT") for item in PERMANENT_SKIP_LIST}:
        score = -999
    if tag == "TRADFI":
        score = max(0, score - 10)
    return int(score)


def _load_research_cache() -> dict | None:
    """Load a previously saved research result, logging malformed cache data."""
    try:
        with open(_RESEARCH_CACHE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("planning_research_cache_unavailable error=%s", exc)
        return None


def _news_for_coin(coin: dict) -> dict:
    """Refresh coin news classification, with the cached research news as fallback."""
    cached = coin.get("news") if isinstance(coin.get("news"), dict) else _neutral_news()
    symbol = str(coin.get("symbol", ""))
    change = coin.get("price24hPcnt", 0)
    prompt = f"Coin: {symbol}. Recent context: {symbol} {change}% today."
    try:
        parsed = openrouter.parse_json_response(openrouter.complete(prompt, system_prompt=_NEWS_SYSTEM_PROMPT))
    except Exception as exc:
        logger.error("planning_news_error symbol=%s error=%s", symbol, exc)
        parsed = None
    if not isinstance(parsed, dict):
        return cached
    return {
        "sentiment": str(parsed.get("sentiment", "NEUTRAL")).upper(),
        "risk_events": parsed.get("risk_events", []),
        "unlock_today": bool(parsed.get("unlock_today", False)),
        "exploit_today": bool(parsed.get("exploit_today", False)),
    }


def _format_planning_card(setups: list[dict]) -> str:
    """Format the top-three ranked setup summary as Telegram HTML."""
    lines = ["<b>📋 Bybit Sniper Planning — Top Setups</b>"]
    if not setups:
        return lines[0] + "\nNo qualifying setups after deterministic scoring."
    for index, setup in enumerate(setups, start=1):
        sr = setup["sr"]
        lines.extend([
            f"<b>{index}. {setup['symbol']}</b> — {setup['score']} pts ({setup['tag']})",
            f"Zone: ${sr['entry_zone_bottom']:,.6g}–${sr['entry_zone_top']:,.6g} | SL: ${sr['sl_level']:,.6g}",
            f"Confluence: {sr['confluence_score']:.1f} | Gap: {sr['gap_to_zone_pct']:.2f}%",
        ])
    return "\n".join(lines)


def run_planning(research_result: dict | None = None) -> list[dict]:
    """Score research candidates, publish the top three, and defer execution if absent."""
    research = research_result if research_result is not None else _load_research_cache()
    if not research:
        telegram.send_message("⚠️ <b>Planning unavailable</b> — research cache is missing or invalid")
        return []
    if research.get("halt_research"):
        logger.warning("planning_skipped_research_halted")
        return []

    setups: list[dict] = []
    for coin in research.get("qualified_coins", []):
        symbol = str(coin.get("symbol", ""))
        current_price = _float(coin.get("price"))
        if not symbol or current_price <= 0:
            logger.warning("planning_coin_missing_price symbol=%s", symbol)
            continue
        flow = data_aggregator.get_capital_flow(symbol)
        if not flow:
            logger.warning("planning_flow_unavailable symbol=%s", symbol)
            continue
        sr = sr_calculator.get_sr_levels(symbol, current_price)
        news = _news_for_coin(coin)
        score = score_coin(symbol, flow, news, str(coin.get("tag", "")))
        if score < 0:
            continue
        setups.append({
            "symbol": symbol,
            "score": score,
            "tag": str(coin.get("tag", "")),
            "current_price": current_price,
            "flow": flow,
            "sr": sr,
            "news": news,
        })
    top_setups = sorted(setups, key=lambda item: item["score"], reverse=True)[:3]
    telegram.send_message(_format_planning_card(top_setups))
    try:
        from bybit_bot import execution
        for setup in top_setups:
            execution.generate_card(setup)
    except Exception as exc:
        logger.error("execution_chain_error error=%s", exc)
        telegram.send_message("⚠️ <b>Execution-card chain failed</b> — planning results were sent")
    return top_setups
