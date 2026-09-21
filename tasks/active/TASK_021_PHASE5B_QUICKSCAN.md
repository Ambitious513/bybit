# TASK-021 — Phase 5B: Quick Scan + Deep Dive Command
## Status: PENDING | Assigned to: Codex | Depends on: TASK-020 COMPLETED

---

## 1. Objective
Build quickscan.py (30-minute lightweight opportunity screener) and the
/deepdive SYMBOL Telegram command (single-coin full pipeline on demand).
Also add BTC regime flip cancellation alert to the existing 15-minute BTC
check in orchestrator.py.

## 2. Background
The 4H research cycle is blind to opportunities that emerge between runs.
Real examples: LAB reached 7.23:1 whale ratio in a 4H gap; TAO was already
running by the time the 18:00 research ran. The quickscan bridges this gap
with a 6-API-call screener that fires only on high-conviction signals.
The /deepdive command provides a full card for any alert without waiting
for the next 4H cycle.

## 3. Source-of-Truth Documents
- Quant analysis doc (quickscan design, conviction thresholds, alert format)
- TASK-020 deliverables (monitor must be complete)
- This task contract

## 4. Scope
Create inside bybit_bot/:
  quickscan.py
Modify:
  orchestrator.py — add quickscan job (30M), add /deepdive handler,
                    add BTC regime flip cancel to 15M BTC check

## 5. Allowed Files / Directories
- bybit_bot/quickscan.py (NEW)
- bybit_bot/orchestrator.py (MODIFY — 3 targeted additions)
- bybit_bot/tests/test_quickscan.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- Weekend escalation, thin volume — TASK-022
- Existing monitor.py checks — DO NOT MODIFY

## 7. Requirements

### 7.1 quickscan.py

HIGH_CONVICTION_THRESHOLD (module-level dict):
{
  "whale_ratio_min":  2.5,
  "fund_side":        "Bullish",
  "volume_min_usd":   5_000_000,
  "price_change_min": 8,
  "price_change_max": 40
}

`run_quickscan() -> list[dict]`
  Guard: call should_run_quickscan() first → if False return []
  STEP 1: get_all_linear_tickers() → 1 API call
  STEP 2: Filter:
    turnover24h >= 5_000_000
    price24hPcnt > 8.0 AND < 40.0
    symbol not in PERMANENT_SKIP_LIST
    symbol not in SESSION_SKIP_LIST
  STEP 3: Sort by price24hPcnt descending, take top 5
  STEP 4: For each top-5 candidate, fetch get_capital_flow(symbol) concurrently
           via ThreadPoolExecutor (5 concurrent calls max)
  STEP 5: Score each by whale_ratio + fund_side only (no LLM, no S/R):
    score = 0
    if flow["longShortRatio"] >= HIGH_CONVICTION_THRESHOLD["whale_ratio_min"]
       and flow["fundSide"] == HIGH_CONVICTION_THRESHOLD["fund_side"]:
       score = 1  # HIGH_CONVICTION
    else:
       score = 0
  STEP 6: For each HIGH_CONVICTION coin → fire Telegram alert (format below)
  STEP 7: Return list of high-conviction hits

`should_run_quickscan() -> bool`
  load research_cache.json → check last research timestamp
  if (utcnow - last_research) < 20 minutes → return False  # full research just ran
  load active_orders.json → count FILLED orders
  if count >= MAX_TRADES_PER_SESSION → return False
  if datetime.utcnow().hour >= HARD_CLOSE_UTC_HOUR → return False
  return True

`format_quickscan_alert(coin: dict, flow: dict) -> str`
  Returns formatted HTML string:
  "⚡ <b>QUICK SCAN ALERT — {HH:MM} UTC</b>
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔥 <b>HIGH CONVICTION DETECTED</b>

  <b>{SYMBOL}</b>
    Price:  ${price}  ({price24hPcnt:+.1f}% 24H)
    Whale:  {whale_ratio:.1f}:1 NET LONG
    Fund:   BULLISH
    Volume: ${volume_m:.1f}M ✅

  → Full research NOT yet run for this coin
  → S/R zones NOT calculated yet
  → This is an <b>EARLY WARNING only</b>

  ACTION: /deepdive {symbol_without_usdt} for full card
          OR wait for next research at [next_4H_time] UTC
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️ Do NOT trade without full S/R data"

### 7.2 orchestrator.py additions

ADDITION 1 — Quickscan scheduler job:
  In daemon mode, add interval job: minutes=30
  Job function: quickscan.run_quickscan()
  Note in comment: "Skips if full research ran in last 20M (handled inside quickscan)"

ADDITION 2 — /deepdive command handler:
  Parse Telegram updates for /deepdive SYMBOL message
  OR accept CLI: python orchestrator.py --deepdive SYMBOL
  Flow:
    1. symbol = args[1].upper() + "USDT" (if not already ending in USDT)
    2. price = get_ticker(symbol)["price"] — if None → send "Symbol not found"
    3. flow = data_aggregator.get_capital_flow(symbol) — if None → send "Flow unavailable"
    4. sr = sr_calculator.get_sr_levels(symbol, float(price))
    5. news = openrouter.complete(coin_news_prompt_for(symbol)) or neutral fallback
    6. score = planning.score_coin(symbol, flow, news, "CRYPTO")
    7. setup = {symbol, score, tag="CRYPTO", current_price=float(price), flow, sr, news}
    8. if score >= 0:
         card = execution.generate_card(setup)
         if card: execution.send_execution_card_telegram(card)
         else: telegram.send_message("⚠️ Stop too wide — skip {symbol}")
       else:
         telegram.send_message("⚠️ {symbol} score {score} — below threshold")
  Completes in < 15 seconds. Log "deepdive_completed" with symbol and score.

ADDITION 3 — BTC regime flip cancel alert (in 15M BTC check):
  In existing btc_check() function (or create it if not yet written):
    current_regime = classify_btc_regime_quick()  # whale_ratio + fund_side only
    prev_regime = load_btc_regime_from_cache()
    if prev_regime == "BULLISH" and current_regime in ["BEARISH", "CHOPPY"]:
      pending_symbols = [o["symbol"] for o in load_active_orders()
                         if o["status"] == "PENDING"]
      if pending_symbols:
        msg = ("⚠️ <b>BTC REGIME FLIP → {current_regime}</b>\n"
               "→ Cancel all pending limit orders NOW\n"
               "→ Pending: {', '.join(pending_symbols)}")
        telegram.send_message(msg)
      update_regime_in_cache(current_regime)

`classify_btc_regime_quick() -> str`
  Lightweight version: only calls get_capital_flow("BTCUSDT")
  Apply primary regime logic from research.py (whale_ratio + fund_side only)
  Returns "BULLISH"|"BEARISH"|"CHOPPY"
  Used only for 15M change detection — does NOT replace full research

## 8. Non-Goals
- Do NOT implement full research pipeline in deepdive — reuse existing modules
- Do NOT implement Weekend escalation or thin volume checks — TASK-022
- Do NOT build a full Telegram polling server — Telegram command handling
  is simplified (parse updates endpoint or CLI flag)

## 9. Interfaces / Contracts
run_quickscan() returns list[dict]:
  [{symbol, price, price24hPcnt, whale_ratio, fund_side, volume}]

/deepdive produces either: execution card in Telegram, or a rejection message.

should_run_quickscan() returns bool (pure function, no side effects).

## 10. Acceptance Criteria
- [ ] run_quickscan() completes in < 5 seconds
- [ ] Max 6 API calls per quickscan run (1 tickers + 5 flow calls)
- [ ] should_run_quickscan() returns False if research ran < 20 min ago
- [ ] should_run_quickscan() returns False if 3 trades already open
- [ ] should_run_quickscan() returns False after 20:00 UTC
- [ ] Coin with whale_ratio=3.0 Bullish fund → HIGH_CONVICTION → alert fires
- [ ] Coin with whale_ratio=2.0 Bullish fund → below threshold → no alert
- [ ] /deepdive TAOUSDT delivers full execution card to Telegram
- [ ] /deepdive INVALID → "Symbol not found" response
- [ ] BTC regime flip BULLISH→BEARISH → pending order cancel alert fires

## 11. Required Tests
File: bybit_bot/tests/test_quickscan.py
- test_should_run_quickscan_false_recent_research — < 20 min → False
- test_should_run_quickscan_false_max_trades — 3 filled → False
- test_should_run_quickscan_false_after_hard_close — hour >= 20 → False
- test_should_run_quickscan_true — all guards pass → True
- test_quickscan_filters_by_volume — < $5M → excluded
- test_quickscan_filters_by_price_change — < 8% or > 40% → excluded
- test_quickscan_high_conviction_fires_alert — whale 3.0 Bullish → alert
- test_quickscan_low_whale_no_alert — whale 2.0 → no alert
- test_quickscan_max_5_flow_calls — only 5 candidates fetched regardless of size
- test_regime_flip_detects_bullish_to_bearish — alert with pending symbols
- test_regime_flip_no_alert_if_no_pending — regime flips but no pending orders

## 12. Expected Deliverables
- bybit_bot/quickscan.py
- bybit_bot/orchestrator.py (modified — 3 additions)
- bybit_bot/tests/test_quickscan.py

## 13. Failure / Escalation Conditions
STOP if: Telegram update polling requires webhook setup (use getUpdates polling
instead); classify_btc_regime_quick diverges significantly from research.py logic.

## 14. Completion Report Requirements
Standard format. Recommended Next Step: TASK-022 Advanced Monitor + Commands.

## 15. Review Plan
Sonnet: verify conviction threshold values match quant spec.
Gemini: adversarial — quickscan fires during full research cycle (duplicate alerts).

## 16. Skill Extraction Decision
NO SKILL — wait for full system validation.

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-020 COMPLETED
Approved by: Lead CTO 2026-09-21
