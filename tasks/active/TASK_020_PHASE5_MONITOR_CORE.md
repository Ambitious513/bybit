# TASK-020 — Phase 5: Position Monitor (Core)
## Status: PENDING | Assigned to: Codex | Depends on: TASK-019 COMPLETED

---

## 1. Objective
Build the core monitoring loop in monitor.py. Runs every 2 minutes via
scheduler. Checks all FILLED orders in active_orders.json against live price
for SL, TP1, TP2, TP3, time-stop, hard-close, and BTC regime flip.
Fires Telegram alerts and updates order status on every trigger.

## 2. Background
This is the safety net. Once the user confirms a fill via /filled command
(TASK-022), monitor.py manages the position lifecycle autonomously —
alerting the user at every critical price event. No orders are placed;
all Telegram messages are instructions for the user to act on manually.

## 3. Source-of-Truth Documents
- TASK-019 deliverables (active_orders.json schema must be complete)
- Spec doc section: "PHASE 5 — monitor.py" (core checks 0-7)
- This task contract

## 4. Scope
Create inside bybit_bot/:
  monitor.py (core checks 0 through 7 only)
Modify:
  orchestrator.py — wire --monitor CLI flag and 2-minute scheduler job

## 5. Allowed Files / Directories
- bybit_bot/monitor.py (NEW)
- bybit_bot/data/active_orders.json (READ + WRITE)
- bybit_bot/data/trade_log.json (NEW — appended on close)
- bybit_bot/data/paper_balance.json (NEW — updated on close)
- bybit_bot/orchestrator.py (MODIFY — wire scheduler)
- bybit_bot/tests/test_monitor.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- Weekend escalation, dead cat, thin volume — TASK-021 / TASK-022
- Telegram command handlers — TASK-022

## 7. Requirements

### 7.1 monitor.py — Core Loop

`run_monitor_cycle() -> None`
  Load active_orders.json → list of orders
  If empty → log "no_open_orders" → return immediately
  For each order:
    price = get_ticker(order["symbol"])["price"]
    btc = get_ticker("BTCUSDT")
    btc_price = btc["price"]
    btc_regime = load_btc_regime_from_cache()  # reads research_cache.json
    alerts, updated_order = check_order(order, float(price),
                                        float(btc_price), btc_regime)
    For each alert: telegram.send_message(alert)
  Save updated orders list back to active_orders.json (atomic write)
  Append closed orders to trade_log.json
  Update paper_balance.json if any order closed

`check_order(order, current_price, btc_price, btc_regime) -> tuple[list[str], dict]`
  Implements checks 0-7 exactly as spec defines:

  CHECK 0 — Skip if not FILLED, check expiry if PENDING:
    if order["status"] == "PENDING":
      if datetime.utcnow() > parse_iso(order["expiry_utc"]):
        alert = "⏰ ORDER EXPIRED {symbol}\n→ Cancel limit order on Bybit now"
        order["status"] = "EXPIRED"
      return alerts, order

  CHECK 1 — SL hit:
    LONG:  current_price <= order["sl"]
    SHORT: current_price >= order["sl"]
    If hit: pnl = calculate_pnl(order, current_price, pct=1.0)
    alert = "🔴 SL HIT {symbol} at ${price}\n→ CLOSE ALL NOW\n→ Paper P&L: {pnl:+.2f}"
    order["status"] = "CLOSED"
    log_trade(order, current_price, "SL")
    return immediately (no further checks)

  CHECK 2 — TP1 / CORE (only if core_closed==False):
    LONG:  current_price >= order["tp1"]
    SHORT: current_price <= order["tp1"]
    If hit: core_pnl = calculate_pnl(order, order["tp1"], pct=CORE_PCT)
    alert = "✅ TP1 HIT {symbol} at ${tp1}\n→ CLOSE CORE (50%) NOW\n
             → Move SL to entry ${entry} (breakeven)\n
             → Paper core P&L: +${core_pnl:.2f}\n
             → Runner targeting TP2 ${tp2}"
    order["core_closed"] = True
    order["sl"] = order["entry"]   # move to breakeven

  CHECK 3 — TP2 (only if core_closed and not runner_closed):
    LONG:  current_price >= order["tp2"]
    SHORT: current_price <= order["tp2"]
    If hit: alert = "🎯 TP2 HIT {symbol} at ${tp2}\n
                     → CLOSE 60% OF RUNNER NOW\n
                     → Let remaining 40% ride to TP3 ${tp3}"

  CHECK 4 — TP3 / Final (only if core_closed and not runner_closed):
    LONG:  current_price >= order["tp3"]
    SHORT: current_price <= order["tp3"]
    If hit: full_pnl = calculate_pnl(order, current_price, pct=1.0)
    alert = "🏆 TP3 HIT {symbol} — FULL CLOSE\n→ Close remaining runner\n
             → Full paper P&L: +${full_pnl:.2f}"
    order["runner_closed"] = True
    order["status"] = "CLOSED"
    log_trade(order, current_price, "TP3")

  CHECK 5 — Time-stop (2H from fill, no TP1 hit):
    if not order["core_closed"] and order["fill_time_utc"]:
      elapsed = datetime.utcnow() - parse_iso(order["fill_time_utc"])
      if elapsed >= timedelta(hours=TIME_STOP_HOURS):
        alert = "⏰ TIME-STOP {symbol}\n→ 2 hours since fill, no TP1 hit\n
                 → CLOSE ALL NOW at ${current_price}"

  CHECK 6 — Hard close:
    if datetime.utcnow().hour >= HARD_CLOSE_UTC_HOUR:
      alert = "🔴 HARD CLOSE — {HARD_CLOSE_UTC_HOUR}:00 UTC\n→ CLOSE ALL POSITIONS NOW"

  CHECK 7 — BTC regime flip (only for LONG orders):
    if btc_regime != "BULLISH" and order["side"] == "LONG":
      alert = "⚠️ BTC REGIME FLIP → {btc_regime}\n
               → CLOSE ALL LONGS NOW\n→ {symbol} at ${current_price}"

`calculate_pnl(order: dict, exit_price: float, pct: float = 1.0) -> float`
  LONG:  pnl = (exit_price - order["entry"]) / order["entry"] * order["notional"] * pct
  SHORT: pnl = (order["entry"] - exit_price) / order["entry"] * order["notional"] * pct
  Return float rounded to 2 decimal places

`log_trade(order: dict, exit_price: float, exit_reason: str) -> None`
  Append to data/trade_log.json:
  {
    "symbol": order["symbol"],
    "side": order["side"],
    "entry": order["entry"],
    "exit_price": exit_price,
    "exit_reason": exit_reason,   # "SL" | "TP1" | "TP3" | "TIME_STOP" | "HARD_CLOSE"
    "pnl": float,
    "paper_risk": order["paper_risk"],
    "score": order.get("score", 0),
    "opened_at": order.get("fill_time_utc"),
    "closed_at": datetime.utcnow().isoformat()
  }

`update_paper_balance(pnl: float) -> None`
  Load data/paper_balance.json (or create default if absent):
  {"balance": PAPER_BALANCE, "total_trades": 0, "wins": 0,
   "losses": 0, "total_pnl": 0.0}
  Update: balance += pnl, total_trades += 1
  If pnl > 0: wins += 1 else: losses += 1
  total_pnl += pnl
  Write back atomically
  If balance < PAPER_BALANCE_FLOOR:
    telegram.send_message("⚠️ BALANCE WARNING: ${balance:.2f} below floor ${PAPER_BALANCE_FLOOR}\n
                           → Review performance before next trade")

`load_btc_regime_from_cache() -> str`
  Load bybit_bot/data/research_cache.json
  Return research_result["regime"]["regime"]   # "BULLISH"|"BEARISH"|"CHOPPY"
  If cache absent or stale (> 5H old) → return "CHOPPY" (safe default)
  Log warning if stale

## 8. Non-Goals
- Weekend escalation, dead cat in-zone check, thin volume — TASK-022
- Telegram /filled /closed commands — TASK-022
- No order placement on Bybit — ever

## 9. Interfaces / Contracts
active_orders.json schema (from TASK-019) extended with:
  fill_time_utc: str | null   (set by /filled command in TASK-022)
  status: "PENDING" | "FILLED" | "CLOSED" | "EXPIRED"
  core_closed: bool
  runner_closed: bool

trade_log.json: JSON array, append-only
paper_balance.json: single JSON object (not array)

## 10. Acceptance Criteria
- [ ] Empty active_orders.json → cycle completes silently in < 1 second
- [ ] PENDING order past expiry → "ORDER EXPIRED" alert fires
- [ ] FILLED LONG order, price <= SL → "SL HIT" alert, status=CLOSED
- [ ] FILLED LONG order, price >= TP1 → "TP1 HIT", core_closed=True, sl moved to entry
- [ ] After TP1, price >= TP2 → "TP2 HIT" alert
- [ ] After TP1, price >= TP3 → "TP3 HIT", runner_closed=True, status=CLOSED
- [ ] 2H elapsed without TP1 → "TIME-STOP" alert
- [ ] Hour >= 20 UTC → "HARD CLOSE" alert for any FILLED order
- [ ] BTC regime BEARISH + LONG order → "REGIME FLIP" alert
- [ ] SL hit → trade_log.json entry created, paper_balance.json updated
- [ ] Balance < 14.00 → balance warning Telegram message fires

## 11. Required Tests
File: bybit_bot/tests/test_monitor.py
- test_empty_orders_returns_immediately
- test_pending_expired_sets_expired_status
- test_sl_hit_long — price at SL → SL alert, status=CLOSED
- test_sl_hit_short — inverse direction correct
- test_tp1_hit_moves_sl_to_entry — core_closed=True, sl==entry
- test_tp2_fires_only_after_core_closed
- test_tp3_closes_position — runner_closed=True, status=CLOSED
- test_time_stop_fires_after_2h — fill_time_utc 3H ago, no TP1 → alert
- test_hard_close_fires_at_20_utc — mock UTC hour = 20 → alert
- test_btc_regime_flip_for_long — regime BEARISH → alert for LONG
- test_btc_regime_flip_no_alert_for_short — SHORT not affected by BULLISH check
- test_calculate_pnl_long_profit — correct math
- test_calculate_pnl_short_profit — correct inverse math
- test_balance_warning_below_floor — balance 13.50 → warning fires
- test_log_trade_appends_to_file — two closes → two entries

## 12. Expected Deliverables
- bybit_bot/monitor.py
- bybit_bot/data/trade_log.json (runtime, auto-created)
- bybit_bot/data/paper_balance.json (runtime, auto-created)
- bybit_bot/tests/test_monitor.py

## 13. Failure / Escalation Conditions
STOP if: active_orders.json corrupted → do not overwrite, alert + skip;
parse_iso fails on fill_time_utc format; check order loop raises unhandled exception.

## 14. Completion Report Requirements
Standard format. Recommended Next Step: TASK-021 Quick Scan + Deep Dive.

## 15. Review Plan
Sonnet: verify P&L math for LONG and SHORT, time-stop edge cases.
Gemini: adversarial — both TP1 and SL triggered same cycle (price gap), stale cache.

## 16. Skill Extraction Decision
NO SKILL — wait for full system validation.

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-019 COMPLETED
Approved by: Lead CTO 2026-09-21
