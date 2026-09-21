# TASK-022 — Phase 6: Advanced Monitor + Telegram Command Interface
## Status: PENDING | Assigned to: Codex | Depends on: TASK-021 COMPLETED

---

## 1. Objective
Complete monitor.py with advanced checks (weekend escalation, dead cat
in-zone filter, thin volume warning), and implement the full Telegram
command interface (11 commands). This is the final phase — on completion
the bot is fully operational.

## 2. Background
The core monitor (TASK-020) handles TP/SL/time-stop. This task adds the
three advanced checks from the spec and the full user command interface
that allows position lifecycle management via Telegram. After this task
the bot is ready for supervised paper trading.

## 3. Source-of-Truth Documents
- TASK-021 deliverables (all prior tasks must be complete)
- Spec doc: "PHASE 5 — monitor.py" (weekend escalation, dead cat, thin volume)
- Spec doc: "TELEGRAM COMMAND INTERFACE" (11 commands verbatim)
- This task contract

## 4. Scope
Modify:
  bybit_bot/monitor.py — add 3 advanced checks to check_order()
  bybit_bot/orchestrator.py — add Telegram update polling + command dispatch

## 5. Allowed Files / Directories
- bybit_bot/monitor.py (MODIFY — add 3 checks)
- bybit_bot/orchestrator.py (MODIFY — add Telegram polling loop)
- bybit_bot/tests/test_advanced_monitor.py (NEW)
- bybit_bot/tests/test_telegram_commands.py (NEW)

## 6. Forbidden Files / Directories
- src/scanner/ — DO NOT TOUCH
- Any core check logic (0-7) in monitor.py — DO NOT MODIFY existing checks

## 7. Requirements

### 7.1 monitor.py — 3 Advanced Checks (append to check_order())

CHECK 8 — Weekend Escalation (in run_monitor_cycle, for PENDING orders):
  If datetime.utcnow().weekday() in [5, 6]:  # Saturday=5, Sunday=6
    For each PENDING order:
      candles = get_klines(order["symbol"], "5", 3)
      last_2 = _parse_candles(candles)[-2:]   # last 2 completed 5M candles
      if len(last_2) == 2 and all(c["close"] > order["zone_top"] for c in last_2):
        current_price = get_ticker(order["symbol"])["price"]
        alert = ("⚡ <b>WEEKEND ESCALATION {symbol}</b>\n"
                 "Price holding above zone 2+ candles\n"
                 "→ Consider MARKET entry at ${current_price}\n"
                 "→ Adjust SL to nearest 5M support\n"
                 "→ Reply /market {symbol_base} to confirm")

CHECK 9 — Dead Cat Check (in run_monitor_cycle, for PENDING orders):
  For each PENDING order:
    current_price = float(get_ticker(order["symbol"])["price"])
    if order["zone_bottom"] <= current_price <= order["zone_top"]:
      # Price has entered the entry zone
      result = sr_calculator.dead_cat_check(order["symbol"], order["zone_bottom"])
      if result["passed"]:
        alert = ("✅ <b>ENTRY CONFIRMED {symbol}</b>\n"
                 "Dead cat filter PASSED — all 3 checks green\n"
                 "→ Safe to place limit order now")
      else:
        failed = [k for k,v in result.items()
                  if isinstance(v, dict) and not v.get("pass", True)]
        alert = ("⚠️ <b>DEAD CAT WARNING {symbol}</b>\n"
                 f"Failed checks: {', '.join(failed)}\n"
                 "→ Wait for next 5M candle")

CHECK 10 — Thin Volume Warning (TRADFI only, weekday 18:00-20:00 UTC):
  hour = datetime.utcnow().hour
  weekday = datetime.utcnow().weekday()
  if 18 <= hour < 20 and weekday < 5:   # weekday only
    For each FILLED order where tag == "TRADFI":
      distance_to_sl_pct = abs(current_price - order["sl"]) / order["entry"] * 100
      if distance_to_sl_pct < 0.3:
        alert = ("⚠️ <b>THIN LIQUIDITY WARNING {symbol}</b>\n"
                 f"Price ${current_price} approaching SL ${order['sl']}\n"
                 "Low volume window (18-20 UTC)\n"
                 "→ Green 5M close above midpoint = HOLD\n"
                 "→ Red close below midpoint = EXIT manually")

### 7.2 orchestrator.py — Telegram Command Polling

Add a background thread (NOT APScheduler) that polls Telegram updates:
  GET https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30
  Long-poll loop running in daemon thread alongside APScheduler.
  Dispatch each /command to the matching handler below.
  Update offset on each response to avoid re-processing messages.

Implement ALL 11 command handlers:

`handle_filled(symbol: str, price: float, time_str: str) -> None`
  Command: /filled SYMBOL PRICE HH:MM
  1. Find order in active_orders.json with matching symbol and status=PENDING
  2. order["status"] = "FILLED"
  3. order["fill_time_utc"] = today's date + time_str + ":00" as ISO UTC
  4. Save updated active_orders.json
  5. Reply: "✅ Trade logged: {symbol} {side} @ ${price}\nTime-stop clock starts now. 2H limit."

`handle_closed(symbol: str, price: float) -> None`
  Command: /closed SYMBOL PRICE
  1. Find FILLED order with matching symbol
  2. pnl = calculate_pnl(order, price, pct=1.0)
  3. log_trade(order, price, "MANUAL_CLOSE")
  4. update_paper_balance(pnl)
  5. order["status"] = "CLOSED"
  6. Save active_orders.json
  7. load paper_balance.json
  8. Reply: "Trade closed. P&L: {pnl:+.2f} | Balance: ${balance:.2f}"

`handle_cancel(symbol: str) -> None`
  Command: /cancel SYMBOL
  Remove PENDING order with matching symbol from active_orders.json
  Reply: "Order cancelled — {symbol} removed from watchlist"
  If not found: Reply: "No pending order for {symbol}"

`handle_skip(symbol: str) -> None`
  Command: /skip SYMBOL
  Append symbol to SESSION_SKIP_LIST (in-memory, resets on restart)
  Reply: "{symbol} skipped this session"

`handle_status() -> None`
  Command: /status
  For each active order (PENDING or FILLED):
    Fetch current price
    Show: symbol, side, status, entry, current_price, distance to SL%,
          distance to TP1%, time remaining to hard close
  Show paper balance
  Reply formatted status card via send_card()

`handle_btc() -> None`
  Command: /btc
  Call classify_btc_regime_quick() from quickscan.py
  Fetch get_ticker("BTCUSDT")
  Reply: formatted BTC regime card:
  "BTC Quick Check\nPrice: ${price}\nWhale: {ratio}:1\nFund: {side}\nFunding: {rate}\nRegime: {regime}"

`handle_balance() -> None`
  Command: /balance
  Load paper_balance.json
  win_rate = wins/total_trades*100 if total_trades > 0 else 0
  avg_pnl = total_pnl/total_trades if total_trades > 0 else 0
  Reply: "Paper Balance: ${balance:.2f}\nTrades: {total} | W:{wins} L:{losses}\nWin Rate: {win_rate:.0f}%\nAvg P&L: ${avg_pnl:+.2f}\nTotal P&L: ${total_pnl:+.2f}"

`handle_htfltf(symbol: str) -> None`
  Command: /htfltf SYMBOL
  Reply: "⚠️ HTF/LTF CONFLICT {symbol}\n→ Close RUNNER (50%) immediately\n→ Keep CORE with SL at breakeven\n→ Reassess next 15M candle"

`handle_market(symbol: str) -> None`
  Command: /market SYMBOL
  Weekend escalation confirmed by user.
  1. price = get_ticker(symbol + "USDT" if needed)["price"]
  2. sr = sr_calculator.get_sr_levels(symbol, float(price))
  3. Override: risk = PAPER_RISK_MARKET ($1.00)
  4. sl = sr["sl_level"]
  5. Generate a market-entry card using execution.generate_card() with
     risk override and current price as entry
  6. Reply with card

`handle_research() -> None`
  Command: /research
  Call research.run_research() in a background thread (non-blocking)
  Reply immediately: "🔍 Research triggered — card incoming in ~30s"

`handle_help() -> None`
  Command: /help
  Reply with list of all 11 commands and one-line description each.

## 8. Non-Goals
- Do NOT implement a full Telegram bot webhook server — use getUpdates polling
- Do NOT modify any phase 1-5 logic
- Do NOT add new strategy rules or signals

## 9. Interfaces / Contracts
Telegram commands arrive as text messages from TELEGRAM_CHAT_ID only.
Ignore messages from any other chat_id (security).
All command handlers are synchronous — run in the polling thread.
handle_research() spawns a thread to avoid blocking the poll loop.

## 10. Acceptance Criteria
- [ ] Weekend (Sat/Sun), PENDING order, price holds above zone_top 2 candles → alert fires
- [ ] Price enters entry zone, dead cat fails rule2 → "DEAD CAT WARNING" with failed checks listed
- [ ] Price enters zone, dead cat passes all 3 → "ENTRY CONFIRMED" alert
- [ ] TRADFI, 18:30 UTC weekday, price 0.2% from SL → "THIN LIQUIDITY WARNING"
- [ ] /filled LABUSDT 0.0571 14:32 → status FILLED, fill_time set, reply received
- [ ] /closed LABUSDT 0.0580 → P&L calculated, balance updated, reply received
- [ ] /cancel LABUSDT → removed from active_orders.json, reply received
- [ ] /skip LABUSDT → added to SESSION_SKIP_LIST, excluded from next screener
- [ ] /status → shows all open positions with live prices
- [ ] /btc → regime card with live data
- [ ] /balance → win/loss/balance stats
- [ ] /help → all 11 commands listed
- [ ] Messages from unknown chat_id → silently ignored
- [ ] /research → research runs, card arrives within 35 seconds

## 11. Required Tests
File: bybit_bot/tests/test_advanced_monitor.py
- test_weekend_escalation_fires_sat — Saturday + 2 candles above zone → alert
- test_weekend_escalation_no_fire_weekday — weekday → no escalation
- test_dead_cat_in_zone_passed — price in zone, all 3 rules pass → ENTRY CONFIRMED
- test_dead_cat_in_zone_failed — price in zone, rule2 fails → WARNING with "rule2_volume"
- test_dead_cat_not_triggered_outside_zone — price above zone → no check
- test_thin_volume_tradfi_fires — TRADFI 19:00 UTC 0.2% from SL → alert
- test_thin_volume_no_fire_crypto — CRYPTO tag → no thin volume check
- test_thin_volume_no_fire_weekend — weekend → no thin volume check

File: bybit_bot/tests/test_telegram_commands.py
- test_handle_filled_updates_status — status changes to FILLED
- test_handle_filled_unknown_symbol — no matching order → error reply
- test_handle_closed_calculates_pnl — correct pnl and balance update
- test_handle_cancel_removes_pending — order removed from list
- test_handle_skip_adds_to_session_list — symbol in SESSION_SKIP_LIST
- test_handle_unknown_chat_id_ignored — message from other chat → no action
- test_handle_balance_zero_trades — total_trades=0 → no division by zero
- test_handle_market_generates_reduced_risk_card — risk = $1.00

## 12. Expected Deliverables
- bybit_bot/monitor.py (modified — 3 checks added)
- bybit_bot/orchestrator.py (modified — Telegram polling + 11 handlers)
- bybit_bot/tests/test_advanced_monitor.py
- bybit_bot/tests/test_telegram_commands.py

## 13. Failure / Escalation Conditions
STOP if: Telegram getUpdates polling conflicts with APScheduler threads;
/market command generates card with sl >= entry (invalid geometry);
dead cat check called with < 4 candles returned from API.

## 14. Completion Report Requirements
Standard format. After this task completes:
Recommended Next Step: Full system integration test → paper trading go-live.

## 15. Review Plan
Sonnet: verify all 11 command handlers produce correct state changes.
Gemini: adversarial — /filled with wrong symbol format, /closed with no open orders,
        concurrent command + monitor cycle modifying active_orders.json.
CTO final review before paper trading go-live declared.

## 16. Skill Extraction Decision
PENDING — after TASK-022 passes all reviews and paper trading validates
the system, create SKILL: "bybit-sniper-bot-v2-architecture".

## 17. Status / Sign-off
Status: PENDING | Depends on TASK-021 COMPLETED
Approved by: Lead CTO 2026-09-21
