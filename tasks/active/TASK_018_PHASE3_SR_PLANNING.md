# TASK-018 — Phase 3: S/R Calculator + Planning Engine
## Status: PENDING | Assigned to: Codex | Depends on: TASK-017 COMPLETED

---

## 1. Objective
Implement sr_calculator.py (full 9-function S/R engine with dead cat filter)
and planning.py (coin scoring, ranking, top-3 setup selection). On completion,
`python orchestrator.py --plan` reads research_cache.json and delivers a
ranked top-3 setup card to Telegram with calculated S/R zones.

## 2. Background
sr_calculator.py was specified by the quant architect and partially provided.
Codex is authorized (Option 2) to implement all functions from the documented
signatures and docstrings in the spec. planning.py implements deterministic
scoring (no LLM) to rank qualified coins by conviction strength.

## 3. Source-of-Truth Documents
- TASK-016 and TASK-017 deliverables (must be complete)
- Spec doc section: "PHASE 3 — sr_calculator.py" and "PHASE 3 — planning.py"
- This task contract

## 4. Scope
Create inside bybit_bot/:
  sr_calculator.py, planning.py
Modify:
  orchestrator.py — wire --plan CLI flag

## 5. Allowed Files / Directories
- bybit_bot/sr_calculator.py (NEW)
- bybit_bot/planning.py (NEW)
- bybit_bot/orchestrator.py (MODIFY — wire --plan only)
- bybit_bot/tests/test_sr_calculator.py (NEW)
- bybit_bot/tests/test_planning.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- bybit_bot/execution.py — TASK-019
- bybit_bot/monitor.py — TASK-020

## 7. Requirements

### 7.1 sr_calculator.py — Full Implementation

Implement ALL 9 functions + dead_cat_check:

`get_sr_levels(symbol: str, current_price: float) -> dict`
  Fetches 3 timeframes via bybit_api.get_klines():
    5M: interval="5",   limit=200
    4H: interval="240", limit=100
    1D: interval="D",   limit=50
  Parses via _parse_candles()
  Runs _bollinger_bands(), _swing_highs_lows(), _volume_profile()
  Calls _cluster_levels() on merged supports and resistances (tolerance 0.5%)
  Returns final dict (see Section 9 interface)

`_bollinger_bands(ohlcv_5m, ohlcv_4h, ohlcv_1d, period=20, std_dev=2.0) -> dict`
  For each timeframe compute rolling 20-period mean and std using numpy.
  upper = mean + 2*std, middle = mean, lower = mean - 2*std (most recent values).
  Supports: [lower (weight=1), middle (weight=0.5)] per timeframe
  Resistances: [upper (weight=1), middle (weight=0.5)] per timeframe
  Returns: {"supports": list, "resistances": list}

`_swing_highs_lows(ohlcv_5m, ohlcv_4h, ohlcv_1d, neighbors=2) -> dict`
  For each timeframe:
    Swing high: highs[i] == max(highs[i-N:i+N+1])
    Swing low:  lows[i]  == min(lows[i-N:i+N+1])
    Recency weight: i/n (0=oldest, 1=newest)
    Timeframe weights: 5m=1.0, 4h=1.5, 1d=2.0
    Final weight = tf_weight * (0.5 + 0.5 * recency_weight)
    Keep top 5 swing highs and top 5 swing lows per timeframe by weight
  Returns: {"supports": list, "resistances": list}

`_volume_profile(ohlcv_5m, bins=20) -> dict`
  Price range of last 200 5M candles divided into 20 bins.
  Assign each candle's volume to bin containing its close price.
  High-volume bins = strong S/R (price acceptance).
  Low-volume bins = price moves through fast (rejection).
  Top 3 highest-volume bin midpoints below current price → supports (weight=1.5)
  Top 3 highest-volume bin midpoints above current price → resistances (weight=1.5)
  Returns: {"supports": list, "resistances": list}

`_cluster_levels(levels: list, current_price: float, tolerance_pct=0.5) -> list`
  Group levels within tolerance_pct% of each other into clusters.
  For each cluster call _summarize_cluster().
  Sort resulting clusters by level ascending.
  Returns list of cluster dicts.

`_summarize_cluster(cluster: list) -> dict`
  cluster = list of level dicts with {level, method, weight}
  Returns:
  {
    "level": weighted_average_price,
    "top": max(levels in cluster),
    "bottom": min(levels in cluster),
    "score": sum(weights),          # confluence strength
    "methods": list of method names,
    "count": len(cluster)           # how many indicators agreed
  }

`_best_entry_zone(support_clusters: list, current_price: float) -> dict`
  Filter clusters: only those BELOW current_price
  Sort by score descending (strongest confluence first)
  Return the top-scored cluster as the entry zone.
  If no support clusters below price → return {"top": price*0.98,
    "bottom": price*0.99, "score": 0} (fallback zone 1-2% below)

`_find_sl(support_clusters: list, entry_zone_bottom: float) -> float`
  Find deepest confirmed support cluster below entry_zone_bottom.
  Add 0.3% buffer below that cluster's bottom.
  If no cluster found → return entry_zone_bottom * 0.97 (3% SL fallback)

`_top_resistances(resistance_clusters: list, current_price: float, n=3) -> list`
  Filter: only clusters ABOVE current_price
  Sort by level ascending (nearest resistance first)
  Return top n levels (just the float level value, not full cluster dict)

`_parse_candles(raw: list) -> list[dict]`
  raw is [[ts, open, high, low, close, volume, turnover], ...]
  Bybit returns newest-first → reverse to oldest-first
  Return list of {"ts":int,"open":float,"high":float,"low":float,
                  "close":float,"volume":float,"turnover":float}
  Skip any row that cannot be parsed (log warning)

`_empty_result(symbol: str) -> dict`
  Returns a safe default result when kline fetch fails:
  {"symbol":symbol,"current_price":0,"entry_zone_top":0,
   "entry_zone_bottom":0,"entry_mid":0,"sl_level":0,
   "stop_dist_pct":0,"resistances":[],"confluence_score":0,
   "gap_to_zone_pct":0,"error":"insufficient_data"}

`dead_cat_check(symbol: str, entry_zone_bottom: float) -> dict`
  Fetch last 4 closed 5M candles via get_klines(symbol, "5", 5)
  Parse with _parse_candles()
  Apply 3 rules to the MOST RECENT completed candle (index -2, not -1):
    Rule 1 — midpoint close: candle["close"] >= (candle["high"] + candle["low"]) / 2
    Rule 2 — volume participation: candle["volume"] >= 0.70 * mean(prior 3 candles volume)
    Rule 3 — higher low: candle["low"] > prior_candle["low"]
  Returns:
  {
    "passed": bool (all 3 must pass),
    "rule1_midpoint":   {"pass": bool, "close": float, "midpoint": float},
    "rule2_volume":     {"pass": bool, "volume": float, "threshold": float},
    "rule3_higher_low": {"pass": bool, "low": float, "prior_low": float}
  }

### 7.2 planning.py

`score_coin(symbol: str, flow: dict, news_result: dict, tag: str) -> int`
  Implement scoring EXACTLY as specified:
  whale = flow["longShortRatio"]
  score starts at 0, apply all 8 scoring groups from spec verbatim:
    whale_ratio (60/50/40/30/20/5 pts)
    fund_side (+20 Bullish, -30 Bearish)
    top_trader_ratio (+15 if >=2.0, +8 if >=1.5)
    funding_rate (+10 healthy <=0.0001, -15 overcrowded >=0.0005)
    oi_trend (+10 growing >5%, -20 declining <-10%)
    news sentiment (+10 BULLISH, -20 BEARISH)
    news unlock_today → score = -999
    news exploit_today → score = -999
    PERMANENT_SKIP_LIST → score = -999
    TRADFI tag → score = max(0, score - 10)
  Return score (int)

`run_planning(research_result: dict = None) -> list[dict]`
  If research_result is None → load from bybit_bot/data/research_cache.json
  For each qualified_coin:
    Refresh capital flow: data_aggregator.get_capital_flow(symbol)
    Get S/R: sr_calculator.get_sr_levels(symbol, current_price)
    Get news: openrouter.complete(coin_news_prompt) or cached news from research
    score = score_coin(symbol, flow, news, tag)
  Filter: score >= 0
  Sort by score descending
  Return top 3 as setup dicts:
  {
    "symbol": str,
    "score": int,
    "tag": str,
    "current_price": float,
    "flow": dict,
    "sr": dict (from sr_calculator),
    "news": dict
  }
  Format and send top-3 ranking card to Telegram.
  Chain to execution.generate_card() for each setup (stub call if execution.py not yet built).

## 8. Non-Goals
- Do NOT implement execution.py card formatting — TASK-019
- Do NOT run monitor loop — TASK-020
- Do NOT use LLM for scoring — scoring is 100% deterministic Python

## 9. Interfaces / Contracts
sr_calculator.get_sr_levels() returns:
{
  "symbol": str,
  "current_price": float,
  "entry_zone_top": float,
  "entry_zone_bottom": float,
  "entry_mid": float,
  "sl_level": float,
  "stop_dist_pct": float,
  "resistances": list[float],   # [r1, r2, r3] ascending
  "confluence_score": float,
  "gap_to_zone_pct": float
}

planning.run_planning() returns list[dict] (max 3 items), each:
{symbol, score, tag, current_price, flow, sr, news}

## 10. Acceptance Criteria
- [ ] get_sr_levels("BTCUSDT", current_price) returns valid dict with all 7 keys
- [ ] entry_zone_top > entry_zone_bottom (valid zone)
- [ ] sl_level < entry_zone_bottom (SL is below entry)
- [ ] resistances list has 1–3 values all above current_price
- [ ] dead_cat_check returns all 3 rule results with pass/fail
- [ ] score_coin with unlock_today=True returns -999
- [ ] score_coin with whale=9.0 Bullish fund = 80 (60+20)
- [ ] run_planning() returns max 3 setups sorted by score descending
- [ ] Top-3 card arrives in Telegram on --plan command

## 11. Required Tests
- test_bollinger_bands_returns_6_levels — 3 timeframes × 2 levels each
- test_swing_highs_detect_local_maxima — synthetic price series
- test_volume_profile_finds_high_volume_zones — synthetic volume data
- test_cluster_groups_within_tolerance — two levels 0.3% apart → same cluster
- test_cluster_separates_outside_tolerance — two levels 1.5% apart → different clusters
- test_best_entry_zone_below_price — returned zone top < current_price
- test_find_sl_below_entry_zone — sl < entry_zone_bottom
- test_dead_cat_rule1_midpoint_fail — close below midpoint → rule1 False
- test_dead_cat_rule2_volume_fail — volume < 70% threshold → rule2 False
- test_dead_cat_rule3_higher_low_fail — lower low → rule3 False
- test_dead_cat_all_pass — all 3 rules → passed=True
- test_score_coin_unlock_disqualifies — score = -999
- test_score_coin_whale_9_bullish — score = 80
- test_score_coin_bearish_fund_penalty — fund_side Bearish → -30
- test_run_planning_returns_max_3 — more than 3 inputs → only 3 returned
- test_run_planning_excludes_negative_scores — score < 0 filtered out

## 12. Expected Deliverables
- bybit_bot/sr_calculator.py
- bybit_bot/planning.py
- bybit_bot/tests/test_sr_calculator.py
- bybit_bot/tests/test_planning.py

## 13. Failure / Escalation Conditions
STOP if: numpy unavailable; get_klines returns unexpected candle format;
cluster algorithm produces entry_zone_top < entry_zone_bottom.

## 14. Completion Report Requirements
Standard format. Recommended Next Step: TASK-019 Execution Cards.

## 15. Review Plan
Sonnet quant review: verify S/R clustering math, dead cat rules, scoring weights.
Gemini adversarial: test with flat price (no swings), zero volume, single candle.

## 16. Skill Extraction Decision
NO SKILL — wait for full system validation.

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-017 COMPLETED
Approved by: Lead CTO 2026-09-21
