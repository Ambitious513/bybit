# TASK-019 — Phase 4: Execution Card Generator
## Status: PENDING | Assigned to: Codex | Depends on: TASK-018 COMPLETED

---

## 1. Objective
Build execution.py — generates fully formatted execution cards, calculates
all trade parameters (entry, SL, TP1/TP2/TP3, leverage, sizing, validity window),
sends to Telegram in exact spec format, and saves to active_orders.json as PENDING.

## 2. Background
The execution card is the primary output the user acts on. It must contain
every number needed to place the trade manually on Bybit — zero ambiguity.
The bot NEVER places orders. This file generates the card and saves state only.

## 3. Source-of-Truth Documents
- TASK-018 deliverables (sr_calculator + planning must be complete)
- Spec doc section: "PHASE 4 — execution.py"
- Card format spec (exact ASCII art in spec)
- This task contract

## 4. Scope
Create inside bybit_bot/:
  execution.py
Modify:
  orchestrator.py — wire --execute CLI flag
  planning.py — replace stub chain call with real execution.generate_card()

## 5. Allowed Files / Directories
- bybit_bot/execution.py (NEW)
- bybit_bot/data/active_orders.json (runtime output)
- bybit_bot/orchestrator.py (MODIFY — wire --execute only)
- bybit_bot/planning.py (MODIFY — replace stub with real chain call)
- bybit_bot/tests/test_execution.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- bybit_bot/monitor.py — TASK-020
- Any Bybit order placement endpoints — ABSOLUTELY FORBIDDEN

## 7. Requirements

### 7.1 execution.py

`generate_card(setup: dict) -> dict | None`
  setup dict contains: {symbol, tag, current_price, flow, sr, news, score}

  STEP 1 — Validity window calculation:
    gap_pct = max(0, (current_price - sr["entry_zone_top"]) / current_price * 100)
    if gap_pct < 1.0:   window_mins=10,  window_label="URGENT"
    elif gap_pct < 3.0: window_mins=30,  window_label="PATIENT"
    else:               window_mins=60,  window_label="SET & FORGET"
    expiry_utc = datetime.utcnow() + timedelta(minutes=window_mins)

  STEP 2 — Leverage by stop distance:
    stop_dist_pct = sr["stop_dist_pct"]
    if stop_dist_pct < 1.0:   leverage = 10
    elif stop_dist_pct < 3.0: leverage = 5
    elif stop_dist_pct < 5.0: leverage = 5
    elif stop_dist_pct < 8.0: leverage = 3
    else: log "stop_too_wide" and return None   # skip this setup

  STEP 3 — TP calculation:
    entry = sr["entry_mid"]
    sl = sr["sl_level"]
    if tag == "TRADFI":
        tp1 = entry + (entry * (stop_dist_pct/100) * 0.8)
        tp2 = entry + (entry * (stop_dist_pct/100) * 1.5)
        tp3 = entry + (entry * (stop_dist_pct/100) * 2.5)
    else:
        tp1 = entry + (entry * (stop_dist_pct/100) * 1.0)
        tp2 = entry + (entry * (stop_dist_pct/100) * 1.5)
        tp3 = entry + (entry * (stop_dist_pct/100) * 2.5)
    Note: TP direction assumes LONG (entry + offset).
    For SHORT setups (BEARISH regime): TP1 = entry - offset, etc.
    Determine side from regime direction in research_cache.json:
      load regime["direction"] → "LONG" or "SHORT"

  STEP 4 — Position sizing:
    risk = PAPER_RISK_PER_TRADE  ($2.00)
    notional = risk / (stop_dist_pct / 100)
    margin = notional / leverage
    qty = notional / entry

  STEP 5 — Build card dict (exact fields from spec):
    {symbol, side, entry, zone_top, zone_bottom, sl, tp1, tp2, tp3,
     leverage, stop_dist_pct, gap_pct, window_mins, window_label,
     expiry_utc, notional, margin, qty, risk, tag,
     core_closed=False, runner_closed=False, fill_time_utc=None,
     paper_risk=risk, status="PENDING",
     whale_ratio=flow["longShortRatio"], fund_side=flow["fundSide"],
     score=setup["score"]}

  STEP 6 — Format and send Telegram card:
    Use EXACT monospace card format from spec (╔═══ lines).
    See Section 9 for required format.

  STEP 7 — Persist to active_orders.json:
    Load existing list (or [] if file absent/empty)
    Append new card dict
    Write back atomically (write to .tmp file then rename)
    Log "order_saved" with symbol and expiry

`send_execution_card_telegram(card: dict) -> bool`
  Formats the full monospace card using telegram.send_card()
  All float values formatted to appropriate decimal places:
    Price > 100: 2 decimal places
    Price 1-100: 4 decimal places
    Price < 1:   6 decimal places
  Returns True on success

`save_pending_order(card: dict) -> None`
  Atomic write pattern:
    Write to data/active_orders.json.tmp
    Rename to data/active_orders.json
  Ensures no corruption on crash mid-write

### 7.2 Card Format (EXACT — do not deviate)
Must match this structure precisely (monospace via <pre> tag):
```
╔══════════════════════════════════════════╗
║  ⚡ EXECUTION CARD — [SYMBOL] [SIDE]     ║
║  Whale [X.X]:1 | Fund [BULL/BEAR]       ║
╠══════════════════════════════════════════╣
║ BTC: $[price] [regime]                  ║
║ ⏰ HARD CLOSE: [HH:MM] UTC              ║
╠══════════════════════════════════════════╣
║ ⏱️ VALIDITY: [issued] → [expiry] UTC    ║
║ GAP: [X.XX]% — [URGENT/PATIENT/S&F]    ║
╠══════════════════════════════════════════╣
║ ✅ PRE-ENTRY CHECKLIST                  ║
║  □ 15M candle CLOSED ≥ $[zone_bottom]  ║
║  □ 5M candle GREEN with volume         ║
║  □ Candle closes ABOVE its midpoint    ║
║  □ Volume ≥ 70% of prior 3 candles     ║
║  □ BTC holding above $[btc_support]    ║
║  □ No negative news last 5 mins        ║
║  ❌ Any box fails → wait next candle   ║
╠══════════════════════════════════════════╣
║ ENTRY: LIMIT $[entry]                   ║
║ ZONE:  $[zone_bottom] – $[zone_top]    ║
╠══════════════════════════════════════════╣
║ STOP LOSS: $[sl] (−[X.XX]% from entry) ║
║ LEVERAGE:  [N]x                         ║
║ ☠️ BTC loses $[btc_invalidation] → exit ║
║ ☠️ [expiry] UTC passes unfilled→ cancel ║
║ ☠️ No TP1 in 2h → close all            ║
╠══════════════════════════════════════════╣
║ TWO-LAYER TAKE PROFIT                   ║
║ CORE (50%) — NO EXCEPTIONS:            ║
║  TP1: $[tp1]  [+X.XX%]  ~$[paper]      ║
║ RUNNER (50%) — after TP1:              ║
║  TP2: $[tp2]  [+X.XX%]  ~$[paper]      ║
║  TP3: $[tp3]  [+X.XX%]  ~$[paper]      ║
║  Core guaranteed: ~$[X.XX] paper       ║
║  Full target:     ~$[X.XX] paper       ║
╠══════════════════════════════════════════╣
║ SIZING: Risk $[X] | [N]x | Qty ~[X]    ║
╠══════════════════════════════════════════╣
║ POST-TP1: Close CORE → SL to $[entry]  ║
║ HTF/LTF diverge → close RUNNER only    ║
╚══════════════════════════════════════════╝
```

## 8. Non-Goals
- Do NOT place any orders on Bybit — read-only system
- Do NOT implement monitor.py — TASK-020
- Do NOT implement Telegram command handlers (/filled etc.) — TASK-022
- Do NOT modify scoring logic — stays in planning.py

## 9. Interfaces / Contracts
generate_card() returns card dict or None (stop too wide).
active_orders.json is a JSON array of card dicts.
PENDING status means user has not yet confirmed fill.
FILLED status set externally by /filled command (TASK-022).

## 10. Acceptance Criteria
- [ ] generate_card() returns None when stop_dist_pct >= 8%
- [ ] Card dict contains all required fields with correct types
- [ ] active_orders.json is created and appended on each card generation
- [ ] Atomic write: crash during write does not corrupt active_orders.json
- [ ] Card arrives in Telegram in monospace format matching spec layout
- [ ] LONG card: TP1 > entry > SL (correct direction)
- [ ] SHORT card: TP1 < entry < SL (correct direction)
- [ ] TRADFI card: TP1 uses 0.8x multiplier (vs 1.0x for crypto)
- [ ] Leverage=10 for stop < 1%, leverage=5 for 1-5%, leverage=3 for 5-8%
- [ ] paper_risk pct calculations correct: core_pnl = risk * CORE_PCT / (stop_dist_pct/100) * (stop_dist_pct/100 * 1.0)

## 11. Required Tests
File: bybit_bot/tests/test_execution.py
- test_stop_too_wide_returns_none — stop_dist=9% → None
- test_leverage_table_lt1pct — stop 0.8% → leverage 10
- test_leverage_table_1to5pct — stop 3% → leverage 5
- test_leverage_table_5to8pct — stop 6% → leverage 3
- test_tp_long_above_entry — tp1 > entry for LONG
- test_tp_short_below_entry — tp1 < entry for SHORT
- test_tradfi_tp_multiplier — TRADFI tp1 = entry*(1 + stop*0.8)
- test_crypto_tp_multiplier — CRYPTO tp1 = entry*(1 + stop*1.0)
- test_validity_urgent — gap < 1% → URGENT, 10 mins
- test_validity_patient — gap 1-3% → PATIENT, 30 mins
- test_validity_set_forget — gap > 3% → SET & FORGET, 60 mins
- test_active_orders_json_appends — two cards → two entries in file
- test_atomic_write — file not corrupted if write interrupted

## 12. Expected Deliverables
- bybit_bot/execution.py
- bybit_bot/tests/test_execution.py
- bybit_bot/data/active_orders.json (runtime, auto-created)

## 13. Failure / Escalation Conditions
STOP if: card format produces Telegram message > 4096 chars;
stop_dist_pct == 0 (division by zero risk); regime direction unavailable.

## 14. Completion Report Requirements
Standard format. Recommended Next Step: TASK-020 Monitor Core.

## 15. Review Plan
Sonnet: verify TP/SL math for both LONG and SHORT, sizing formula.
Gemini: adversarial — zero price, extreme leverage edge cases.

## 16. Skill Extraction Decision
NO SKILL — wait for full system validation.

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-018 COMPLETED
Approved by: Lead CTO 2026-09-21
