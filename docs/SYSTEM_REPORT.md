# Bybit Sniper Bot v2.0 — System Report
**Prepared by:** Lead CTO / Systems Architect
**Date:** 2026-09-23
**Status:** Paper Trading (GATE-2) — Day 1 of 7-day observation period
**Repository:** `https://github.com/Ambitious513/bybit.git` — branch `master` @ `229f55c`
**VPS:** `72.61.137.76` (Hostinger Ubuntu 24.04) — service `bybit-sniper` active

---

## 1. Purpose and Scope

The Bybit Sniper Bot v2.0 is a **manual-execution paper trading assistant** for Bybit perpetual futures. It does not place orders automatically. Instead, it:

1. Scans the market on a schedule
2. Identifies high-probability setups using a scored, rules-based strategy
3. Sends the operator precise **Execution Cards** via Telegram
4. Monitors open paper positions and enforces risk rules
5. Logs all trades for performance analysis

The system is currently in **paper trading mode**. No real money is at risk. All sizing, P&L, and balance figures are simulated.

---

## 2. Strategy Overview (GATE-1 Locked)

The strategy is defined in `docs/STRATEGY_SPEC.md` and may not be changed without a formal Strategy Change Proposal and human approval.

### Core Logic
- **Trade direction** is determined by the BTC 4H regime (BULLISH → LONG only, BEARISH → SHORT only, CHOPPY → no trade)
- **Entry zones** are identified using Support/Resistance calculation on the target symbol
- **Setups are scored** across multiple factors (whale flow, funding, momentum, news, S/R quality)
- Only **A+ scored setups** (above threshold) receive execution cards
- **Hard close at 20:00 UTC daily** — no new cards issued after this time

### Risk Per Trade
| Mode | Risk |
|---|---|
| Standard (paper) | \$2.00 per trade |
| Caution mode | \$1.00 per trade |
| Market / weekend card | \$1.00 per trade |

### Position Limits
- Maximum **3 simultaneous active trades** (PENDING or FILLED)
- Maximum stop distance: **8%** from entry
- Minimum stop distance: **0.3%** from entry (data anomaly safety guard)
- BTC invalidation level: \$84,200 (hard exit signal)
- Time-stop: **2 hours** after fill — close all if no TP1 hit

### Take Profit Structure (Two-Layer)
- **Core (50%)** → TP1 at `1.0× stop_dist` above entry for CRYPTO, `0.8×` for TRADFI perps
- **Runner (50%)** → TP2 at `1.5×`, TP3 at `2.5×` stop distance, taken after TP1

---

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      APScheduler (UTC)                        │
│  Research:  0,4,8,12,16,20:05    Monitor:    every 2 min     │
│  BTC Check: every 15 min         Quick Scan: every 30 min    │
│  Heartbeat: 00:01 UTC daily                                   │
└──────────┬───────────┬─────────────┬────────────┬────────────┘
           │           │             │            │
    ┌──────▼──────┐ ┌──▼──────┐ ┌───▼──────┐ ┌──▼─────────┐
    │  Research   │ │ Monitor │ │ BTC Check│ │ Quick Scan │
    │  Pipeline   │ │  Core   │ │          │ │            │
    └──────┬──────┘ └──┬──────┘ └──────────┘ └────────────┘
           │           │
    ┌──────▼──────┐    │
    │  Planning   │    │        active_orders.json
    │  + Scoring  │    └──────► [_ORDERS_LOCK — shared mutex]
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │  Execution  │────► Telegram Card ────► Operator action
    │  Card Gen   │
    └─────────────┘

Telegram Poll Thread (daemon) ──► Command Dispatch ──► All handlers
```

---

## 4. Component Walkthrough

### 4.1 Research Pipeline — Every 4 Hours at XX:05 UTC
**File:** `bybit_bot/research.py`

Fires at 00:05, 04:05, 08:05, 12:05, 16:05, and 20:05 UTC.
The **20:05 run is cache-only** — it refreshes BTC regime data but does not produce planning messages or execution cards (market is closed).

**Steps:**
1. Fetch BTC price, funding rate, and whale flow from Bybit API
2. Classify BTC regime: `BULLISH` / `BEARISH` / `CHOPPY`
3. Retrieve Fear & Greed index from external API
4. Screen gainers, standing watchlist, and TRADFI perpetuals via `screener.py`
5. For each candidate: fetch news sentiment (OpenRouter AI), capital flow, open interest
6. Score each coin via `planning.score_coin()` — only A+ scores proceed
7. Calculate S/R levels for qualifying coins via `sr_calculator.py`
8. Send research summary to Telegram
9. Pass top setups to execution card generation
10. Save full result to `data/research_cache.json`

> A non-blocking mutex (`_RESEARCH_LOCK`) prevents the scheduler and a manual `/research` command from running simultaneously.

---

### 4.2 Planning and Scoring — `bybit_bot/planning.py`

For each setup from research:
- Applies the A+ score filter — rejects below-threshold coins
- Sends a planning summary card to Telegram (informational)
- Calls `execution.generate_card()` for each qualifying setup

---

### 4.3 Execution Card Generation — `bybit_bot/execution.py`

`generate_card()` is the final gatekeeper before any card is issued.
All checks run **atomically under a process-wide lock** (`_ORDERS_LOCK`).

**Guards (in order, fail-fast):**

| # | Guard | Result on Fail |
|---|---|---|
| 1 | Hour ≥ 20:00 UTC | No card — hard close |
| 2 | BTC regime cache unavailable | No card |
| 3 | Invalid prices (zero or negative) | No card |
| 4 | Stop distance ≥ 8% | Skip — stop too wide |
| 5 | Stop distance < 0.3% | Skip — data anomaly |
| 6 | Stop on wrong side of entry | No card — bad geometry |
| 7 | 3 active trades already open | Skip — session cap |
| 8 | Symbol already PENDING or FILLED | Skip — duplicate |

**If all guards pass:**
- Computes TP1/TP2/TP3, leverage, notional, position size, expiry window
- Applies 0.8× multiplier for TRADFI perps (XAUUSDT, NVDAUSDT, COINUSDT, MSTRUSDT)
- Saves card atomically to `active_orders.json` (write to `.tmp` → `os.replace()`)
- Sends formatted Execution Card to Telegram
- Returns the card

The **Execution Card** shows: entry, zone, stop loss, TP1/2/3, leverage, notional, pre-entry checklist, validity window, BTC context, and expected P&L at each target.

---

### 4.4 Position Monitor — Every 2 Minutes
**File:** `bybit_bot/monitor.py`

Checks all FILLED orders against 7 exit conditions every 2 minutes:

| Trigger | Action |
|---|---|
| Price ≥ TP1 | Alert: close core (50%), move SL to breakeven |
| Price ≥ TP3 | Alert: close runner (50%), log full trade |
| Price ≤ Stop Loss | Alert: close all, log loss |
| 2H elapsed since fill | Alert: time-stop hit, close all |
| Hour ≥ 20:00 UTC | Alert: hard close, exit all |
| BTC drops below \$84,200 | Alert: BTC invalidation, exit all |
| Extreme funding rate | Warning alert |

After each exit: updates `active_orders.json`, appends to `trade_log.json`, updates `paper_balance.json`. P&L uses the operator's actual fill price (from `/filled` command) when available.

---

### 4.5 BTC Regime Check — Every 15 Minutes
**File:** `bybit_bot/btc_support.py`

- Fetches current BTC price and recalculates regime
- Detects regime flips (e.g., BULLISH → CHOPPY)
- Sends Telegram alert if regime has changed since last research run
- Lightweight — no OpenRouter AI call, no full scan

---

### 4.6 Quick Scan — Every 30 Minutes
**File:** `bybit_bot/quickscan.py`

- Scans gainers and watchlist for early-warning setups
- Data-only (no AI) — fast, low cost
- Sends early Telegram alerts for coins approaching entry zones
- Skips automatically if full research ran in the last 20 minutes

---

### 4.7 Telegram Command Interface

The poll thread runs alongside APScheduler as a daemon thread. It recovers automatically from network errors with exponential backoff (1s → 2s → ... → 60s max). The last-acknowledged Telegram update ID is saved to disk — no command ever replays after a restart.

| Command | Function |
|---|---|
| `/filled SYMBOL PRICE HH:MM` | Mark card filled at actual fill price; starts 2H time-stop |
| `/closed SYMBOL PRICE` | Manual close; logs P&L to trade log |
| `/cancel SYMBOL` | Cancel a pending card |
| `/skip SYMBOL` | Skip symbol for this session |
| `/status` | Show all PENDING and FILLED orders |
| `/balance` | Paper account stats — balance, win rate, P&L breakdown |
| `/btc` | BTC regime, whale flow, funding rate |
| `/research` | Trigger full research cycle immediately |
| `/deepdive SYMBOL` | On-demand analysis and card for any symbol |
| `/market SYMBOL` | Weekend/after-hours card at \$1.00 risk |
| `/htfltf SYMBOL` | HTF/LTF conflict — runner management instruction |
| `/help` | Full command list |

---

## 5. State Management

All runtime state lives in `bybit_bot/data/`:

| File | Contents | Written by |
|---|---|---|
| `research_cache.json` | BTC regime, scored setups, OI history | `research.py` |
| `active_orders.json` | All orders (PENDING/FILLED/CLOSED/CANCELLED) | `execution.py`, `monitor.py`, command handlers |
| `trade_log.json` | Append-only closed trade records | `monitor.py` |
| `paper_balance.json` | Running balance, W/L counts, total P&L | `monitor.py` |
| `tg_offset.json` | Last-acknowledged Telegram update ID | `orchestrator.py` |

**Concurrency safety:** All reads and writes to `active_orders.json` are serialized through `_ORDERS_LOCK` (a `threading.Lock()` shared across all modules). Writes use atomic `tmp → os.replace()` — a mid-write crash cannot corrupt the file.

---

## 6. Operational Safety Features

| Feature | Implementation |
|---|---|
| No real order placement | Zero Bybit order API calls — exchange access is read-only |
| Hard close enforcement | Cards blocked at 20:00 UTC; monitor closes all positions at 20:00 |
| Session trade cap | Max 3 simultaneous PENDING+FILLED trades, enforced atomically |
| Symbol deduplication | Cannot issue a second card for an already-active symbol |
| Stop distance bounds | 0.3%–8.0% — rejects data anomalies at both extremes |
| Lock timeout | Command handlers time out after 10s if lock is held |
| No command replay | Telegram offset persisted to disk across restarts |
| Poll loop resilience | Auto-recovery with exponential backoff on network errors |
| Research mutex | Scheduler and `/research` command cannot overlap |
| Daily heartbeat | Bot sends alive/status message to Telegram at 00:01 UTC |

---

## 7. Test Coverage

**133 tests — all passing on current codebase.**

| File | Focus |
|---|---|
| `test_execution.py` | Card generation, stop guards, persistence |
| `test_monitor.py` | P&L calculation, exit conditions, balance updates |
| `test_state_integrity.py` | Session cap, dedup, hard-close, concurrent writes, fill price |
| `test_reliability.py` | Offset persistence, backoff, poll recovery, research mutex |
| `test_observability.py` | TRADFI tags, risk override, trade log fields, balance label |
| `test_telegram_commands.py` | All Telegram command dispatch and response handling |

> The concurrent write test uses real `threading.Thread` objects (not mocks) to confirm that two simultaneous card writes both persist correctly under the shared lock.

---

## 8. Current Live Status (09:44 UTC, 2026-09-23)

| Check | Status |
|---|---|
| Service | ✅ Active — PID 2167401 |
| Server uptime | ✅ 21 days continuous |
| Monitor | ✅ Firing every 2 min — `no_open_orders` |
| BTC regime check | ✅ Firing every 15 min |
| Quick scan | ✅ Firing every 30 min |
| Research cache | ✅ Updated 09:36 UTC |
| Telegram offset | ✅ Persisted — no replay risk |
| Open positions | ✅ None — clean state |
| Errors | ⚠️ Cosmetic HTTP connection pool warnings during quickscan (non-fatal — all requests succeed) |

---

## 9. Governance and Gate Progression

The project operates under `AGENTS.md` v2.0 — a binding agent constitution covering authority hierarchy, protected files, mandatory review pipeline, and gate controls.

All fix tasks (TASK-023, 024, 025) completed the full review pipeline:
`Codex → Sonnet quant review → Gemini adversarial review → CTO final review`

Artifacts stored in `reviews/sonnet/`, `reviews/gemini/`, `reviews/opus/`.

### Gate Status

| Gate | Description | Status |
|---|---|---|
| GATE-1 | Strategy specification finalized | ✅ 2026-09-21 |
| GATE-2 | Paper trading observation begins | ✅ 2026-09-22 (Day 1 of 7) |
| GATE-3 | Live trading authorization | ⏳ Requires 7-day observation + human approval |
| GATE-4 | Risk parameters increase | ⏳ Post-live authorization |

> **No agent or system component may advance past GATE-2 without explicit human approval.**

---

## 10. Pre-GATE-3 Recommendations

The following items were identified during adversarial review. None block paper trading. All are recommended before live trading:

| Priority | Item |
|---|---|
| P2 | **`/filled` price validation** — reject `price ≤ 0` to prevent corrupt P&L records |
| P2 | **Telegram fallback** — during outage, operator cannot send `/filled` or `/cancel`. Consider SMS/email backup for live trading. |
| P2 | **`TRADFI_PERPS` list audit** — confirm all active TRADFI perpetuals on Bybit are in the config list |
| P3 | **`btc_support` precision** — currently uses entry zone bottom as BTC support proxy. Dedicated lowest-support calculation would improve accuracy (TASK-026 candidate). |
| P3 | **HTTP connection pool** — quickscan concurrency saturates urllib3's pool. Capping concurrent requests removes cosmetic warnings. |

---

*Report prepared by Lead CTO — Bybit Sniper Bot v2.0*
*Source: `https://github.com/Ambitious513/bybit.git`*
*Generated: 2026-09-23*
