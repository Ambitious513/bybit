# TASK-017 — Phase 2: Screener + Research Engine
## Status: PENDING | Assigned to: Codex | Depends on: TASK-016 COMPLETED

---

## 1. Objective
Build screener.py (coin universe filtering) and research.py (full 6-step
research pipeline). On completion, `python orchestrator.py --research`
delivers a formatted research card to Telegram and writes research_cache.json
in under 30 seconds.

## 2. Background
The research pipeline is the bot's primary intelligence layer. It runs every
4H, classifies BTC regime, qualifies coins from 3 sources (gainers, losers,
watchlist), scans TradFi perps, and produces a ranked shortlist for planning.
Capital flow data comes from data_aggregator.get_capital_flow() (TASK-016).

## 3. Source-of-Truth Documents
- TASK-016 deliverables (all foundation files must be complete)
- This task contract
- AGENTS.md

## 4. Scope
Create inside `bybit_bot/`:
  screener.py, research.py
Modify:
  orchestrator.py — wire --research CLI flag to research.run_research()

## 5. Allowed Files / Directories
- bybit_bot/screener.py (NEW)
- bybit_bot/research.py (NEW)
- bybit_bot/orchestrator.py (MODIFY — wire --research only)
- bybit_bot/data/research_cache.json (runtime output)
- bybit_bot/tests/test_research.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- bybit_bot/sr_calculator.py — TASK-018
- bybit_bot/planning.py — TASK-018
- Any protected docs

## 7. Requirements

### 7.1 screener.py
Implement exactly these 3 functions:

`get_gainers(min_volume_usd=1_000_000, limit=10) -> list[dict]`
  1. get_all_linear_tickers()
  2. Filter: turnover24h >= min_volume_usd
  3. Filter: symbol not in PERMANENT_SKIP_LIST (check both with and without USDT suffix)
  4. Filter: symbol not in SESSION_SKIP_LIST
  5. Filter: price24hPcnt > 8.0 AND < 50.0
  6. Sort by price24hPcnt descending
  7. Return top limit results as list of dicts with keys:
     {symbol, price, price24hPcnt, turnover24h, tag="GAINER"}

`get_losers(min_volume_usd=1_000_000, limit=10) -> list[dict]`
  Same pipeline but:
  Filter: price24hPcnt < -10.0
  Sort ascending (most negative first)
  tag="LOSER"
  Only call this when BTC regime == BULLISH (loser reversal plays)

`get_standing_watchlist() -> list[dict]`
  For each symbol in WATCHLIST_STANDING:
    fetch get_ticker(symbol)
    Return list of dicts:
    {symbol, price, price24hPcnt, turnover24h, tag="WATCHLIST",
     qualify_if, skip_if, edge, note}
  Fetch all concurrently via ThreadPoolExecutor
  If ticker fails → skip that symbol, log warning

Fallback: if get_gainers returns 0 results → log "screener_empty"
  → return get_standing_watchlist() result only

### 7.2 research.py
Implement `run_research() -> dict` executing all 6 steps:

STEP 1 — CONCURRENT DATA FETCH via ThreadPoolExecutor:
  a) BTC ticker: get_ticker("BTCUSDT")
  b) BTC capital flow: data_aggregator.get_capital_flow("BTCUSDT")
  c) BTC volatility proxy:
     {
       "price_range_24h_pct": (high - low) / close * 100,  # from ticker
       "funding_magnitude": abs(funding_rate),               # from capital flow
       "crowded": abs(funding_rate) > 0.0005                # derived flag
     }
  d) Fear & Greed: GET https://api.alternative.me/fng/?limit=1
     Extract: {"value": int, "classification": str}
     On failure: return {"value": None, "classification": "UNKNOWN"}
  e) Gainers: screener.get_gainers()
  f) Losers: screener.get_losers()
  g) Standing watchlist: screener.get_standing_watchlist()
  h) TradFi scan: scan_tradfi_perps() (defined below)
  i) BTC news: openrouter.complete(btc_news_prompt) with JSON system prompt

STEP 2 — EVENTS CALENDAR
  Define KNOWN_EVENTS dict at module top (examples from spec)
  Check datetime.utcnow().date() against KNOWN_EVENTS
  EXTREME_EVENT_KEYWORDS = ["FOMC","CPI","PPI","NFP","SEC","DERIBIT QUARTERLY"]
  If today's event contains any keyword → set halt_research=True
    → send Telegram warning → return early (no chain to planning)
  If non-extreme event → include warning in research card

STEP 3 — BTC REGIME CLASSIFICATION (deterministic, no LLM)
`classify_btc_regime(flow, volatility_proxy, fear_greed) -> dict`
  whale_ratio = flow["longShortRatio"]
  fund_side = flow["fundSide"]
  funding_magnitude = volatility_proxy["funding_magnitude"]

  Primary regime:
    if whale_ratio >= BTC_BULL_WHALE_MIN and fund_side == "Bullish":
        regime = "BULLISH"
    elif whale_ratio <= BTC_BEAR_WHALE_MAX and fund_side == "Bearish":
        regime = "BEARISH"
    else:
        regime = "CHOPPY"

  Sub-type:
    sub_type = "NORMAL"
    if regime == "BULLISH":
        if whale_ratio >= BTC_STRONG_MIN: sub_type = "STRONG"
        oi_delta = (flow["openInterestHistory"]["current"] -
                    (flow["openInterestHistory"]["thirtyDaysAgo"] or
                     flow["openInterestHistory"]["current"])) /
                   max(flow["openInterestHistory"]["current"], 1)
        if oi_delta < -0.02: sub_type = "PULLBACK"

  Warnings list (append each that applies):
    if funding_magnitude > 0.0005: warnings.append("CROWDED — funding elevated")
    if volatility_proxy["price_range_24h_pct"] > 8: warnings.append("HIGH_VOLATILITY — tighten stops")
    if fear_greed["value"] and int(fear_greed["value"]) >= 80:
        warnings.append("EXTREME_GREED — reduce size")

  Return dict matching interface in Section 9.

STEP 4 — COIN QUALIFICATION
`qualify_coins(gainers, losers, watchlist, tradfi, regime) -> list[dict]`
  For each coin across all 4 sources:
    INSTANT DISQUALIFY: PERMANENT_SKIP_LIST, SESSION_SKIP_LIST,
      news["unlock_today"], news["exploit_today"]
    GAINERS qualify if: regime == "BULLISH" (direction == LONG)
    LOSERS qualify if: regime == "BULLISH" only (reversal plays)
    WATCHLIST qualify if: meets coin's qualify_if condition from WATCHLIST_STANDING
      (parse qualify_if string, evaluate whale_ratio and fund_side from flow data)
    TRADFI qualify if: whale_ratio >= TRADFI_MIN_WHALE_RATIO=2.0 and fund_side Bullish
  Tag each: GAINER / LOSER / WATCHLIST / TRADFI
  Fetch LLM news for each qualified coin via openrouter:
    system_prompt: return JSON {"sentiment":"BULLISH|BEARISH|NEUTRAL",
                                "risk_events":[],"unlock_today":false,"exploit_today":false}
    user: "Coin: {symbol}. Recent context: {symbol} {price24hPcnt}% today."
  If LLM fails → set news = {"sentiment":"NEUTRAL","unlock_today":false,"exploit_today":false}
  Return qualified list with flow and news attached

STEP 5 — TRADFI PERPS SCAN
`scan_tradfi_perps() -> list[dict]`
  For each symbol in TRADFI_PERPS:
    Concurrently fetch: get_capital_flow(symbol), get_ticker(symbol)
    whale_ratio = flow["longShortRatio"]
    fund_side = flow["fundSide"]
    qualify if: whale_ratio >= 2.0 and fund_side == "Bullish"
    Return: {symbol, price, price24hPcnt, whale_ratio, fund_side, tag="TRADFI"}

STEP 6 — FORMAT AND SEND
Format research card as Telegram HTML (not monospace for research summary).
Include: regime, sub_type, direction, whale_ratio, fund_side, fear_greed,
         warnings, top qualified coins (max 5), any events warning.
Send via telegram.send_message()
Save full result to bybit_bot/data/research_cache.json with ISO timestamp.
Auto-chain to planning.run_planning(research_result) after save.
Return the research result dict.

### 7.3 BTC news prompt template
system: "You are a crypto news analyst. Return JSON only.
         Format: {\"sentiment\": \"BULLISH|BEARISH|NEUTRAL\",
                  \"risk_events\": [\"event1\"],
                  \"unlock_today\": false, \"exploit_today\": false}"
user: "Analyze Bitcoin sentiment from these signals: BTC {price24hPcnt}%
       24H, whale ratio {whale_ratio}:1, fear/greed {value}/100.
       Any obvious risk events? Return JSON."

## 8. Non-Goals
- Do NOT implement sr_calculator.py, planning.py — TASK-018
- Do NOT implement any entry card logic — TASK-019
- Do NOT implement monitor.py — TASK-020
- qualify_if string parsing: simple string match is acceptable (e.g.,
  check if "whale_ratio > 1.3" passes given actual whale_ratio)

## 9. Interfaces / Contracts
classify_btc_regime() returns:
{
  "regime": "BULLISH"|"BEARISH"|"CHOPPY",
  "sub_type": "STRONG"|"PULLBACK"|"BREAKOUT"|"NORMAL",
  "direction": "LONG"|"SHORT"|"CHOP_ONLY",
  "whale_ratio": float,
  "fund_side": str,
  "funding_rate": float,
  "fear_greed": {"value": int|None, "classification": str},
  "warnings": list[str],
  "btc_price": float,
  "long_ok": bool
}

run_research() returns:
{
  "timestamp": str (ISO),
  "regime": dict (from classify_btc_regime),
  "qualified_coins": list[dict],
  "halt_research": bool,
  "events_today": list[str]
}

## 10. Acceptance Criteria
- [ ] `python orchestrator.py --research` completes in < 30 seconds
- [ ] research_cache.json is created with valid JSON and ISO timestamp
- [ ] Research card arrives in Telegram with regime, direction, coins
- [ ] With mock BULLISH regime, gainers > 8% appear in qualified list
- [ ] With mock BEARISH regime, gainers are NOT in qualified list
- [ ] Extreme event today → research halts, no card chains to planning
- [ ] LLM failure → research completes with neutral news (no crash)

## 11. Required Tests
File: `bybit_bot/tests/test_research.py`
- test_classify_btc_regime_bullish — whale>1.05, fund Bullish → BULLISH
- test_classify_btc_regime_bearish — whale<0.95, fund Bearish → BEARISH
- test_classify_btc_regime_choppy — whale=1.0, fund Bullish → CHOPPY
- test_classify_sub_type_strong — whale >= 1.30 → STRONG
- test_gainers_filter_excludes_skip_list — PERMANENT_SKIP_LIST symbols absent
- test_gainers_filter_range — only 8%-50% included
- test_losers_only_in_bullish_regime — losers list empty when BEARISH
- test_qualify_coins_instant_disqualify — unlock_today=True → excluded
- test_extreme_event_halts_research — FOMC in events → halt_research=True
- test_research_cache_written — run_research() creates valid JSON file
- test_tradfi_scan_qualifies_high_whale — whale >= 2.0 + Bullish → included

## 12. Expected Deliverables
- bybit_bot/screener.py
- bybit_bot/research.py
- bybit_bot/tests/test_research.py
- bybit_bot/data/research_cache.json (runtime, auto-created)

## 13. Failure / Escalation Conditions
STOP if: Fear & Greed API changes schema; Bybit account-ratio endpoint
returns unexpected format; qualify_if evaluation logic is ambiguous.

## 14. Completion Report Requirements
Same format as TASK-016 Section 14.
Recommended Next Step: TASK-018 S/R Calculator + Planning Engine

## 15. Review Plan
Sonnet: verify regime classification logic matches strategy spec
Gemini: adversarial test — what if all API calls fail simultaneously?

## 16. Skill Extraction Decision
NO SKILL — wait for full system validation.

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-016 COMPLETED
Approved by: Lead CTO 2026-09-21
