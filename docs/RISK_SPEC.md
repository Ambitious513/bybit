# RISK_SPEC.md — Bybit Sniper Bot v2.0
# Version: 2.0 | Status: GATE-1 APPROVED | Date: 2026-09-21
# Authority: Human-approved. Protected. Read-only for all agents.

---

## 1. PAPER TRADING PARAMETERS (IMMUTABLE)

```python
PAPER_BALANCE          = 18.66   # current paper capital
PAPER_RISK_PER_TRADE   = 2.00    # normal trade risk
PAPER_RISK_CAUTION     = 1.00    # caution days / uncertain market
PAPER_RISK_MARKET      = 1.00    # weekend market entry escalation
PAPER_BALANCE_FLOOR    = 14.00   # suspend trading below this
```

**Balance floor rule:** If paper_balance < $14.00:
- Send Telegram warning immediately
- No new trades until reviewed by human

---

## 2. SESSION LIMITS (IMMUTABLE)

```python
MAX_TRADES_PER_SESSION = 3    # maximum concurrent / daily positions
```

---

## 3. LEVERAGE TABLE (IMMUTABLE)

Leverage is determined by stop distance — not manually set:

```
stop_dist_pct < 1%:    leverage = 10x
stop_dist_pct 1–3%:   leverage = 5x
stop_dist_pct 3–5%:   leverage = 5x
stop_dist_pct 5–8%:   leverage = 3x
stop_dist_pct > 8%:   SKIP — stop too wide, no trade

Caution day override: halve all leverage values
```

```python
MAX_LEVERAGE        = 10
CAUTION_MAX_LEVERAGE = 5
```

---

## 4. POSITION SIZING FORMULA (IMMUTABLE)

```python
risk      = PAPER_RISK_PER_TRADE          # $2.00
notional  = risk / (stop_dist_pct / 100)  # position size
margin    = notional / leverage            # margin required
qty       = notional / entry_price        # units
```

---

## 5. TWO-LAYER TP SPLIT (IMMUTABLE)

```python
CORE_PCT   = 0.50   # close 50% at TP1 — NO EXCEPTIONS EVER
RUNNER_PCT = 0.50   # hold 50% after TP1 with trailing SL
```

---

## 6. TIME-BASED RISK CONTROLS (IMMUTABLE)

```python
TIME_STOP_HOURS      = 2    # close all if no TP1 hit within 2H of fill
HARD_CLOSE_UTC_HOUR  = 20   # close all positions at 20:00 UTC daily
```

---

## 7. SIGNAL EXPIRATION (IMMUTABLE)

```python
WINDOW_URGENT_MINS     = 10   # gap < 1% — cancel in 10 min if unfilled
WINDOW_PATIENT_MINS    = 30   # gap 1-3% — cancel in 30 min if unfilled
WINDOW_SET_FORGET_MINS = 60   # gap > 3% — cancel in 60 min if unfilled
MAX_CHASE_PCT          = 3.0  # cancel if price >3% past zone unfilled
```

---

## 8. QUICK SCAN CONVICTION THRESHOLD (IMMUTABLE)

```python
HIGH_CONVICTION_THRESHOLD = {
    "whale_ratio_min":   2.5,
    "fund_side":         "Bullish",
    "volume_min_usd":    5_000_000,
    "price_change_min":  8,
    "price_change_max":  40,
}
```

---

## 9. TRADFI-SPECIFIC RULES

```
TRADFI coins (COIN, MSTR, XAU, NVDA) use:
  TP1 multiplier: 0.8x stop distance (tighter, less volatile TP)
  Thin volume window: 18:00–20:00 UTC weekdays
    → Alert if price within 0.3% of SL during this window
  Weekend: most reliable for XAUUSDT (gold trades 24/7)
  NVDAUSDT: only scan when semis had big prior-day move
```

---

## 10. BTC INVALIDATION

```python
BTC_INVALIDATION = 84200   # update each session from S/R data
```

If BTC loses this level → exit ALL positions immediately regardless of TP/SL state.

---

## 11. TRADING PROGRESSION (GATE REQUIREMENTS)

```
DEVELOPMENT → UNIT TESTS PASS → INTEGRATION TESTS PASS
    ↓
PAPER TRADING (GATE-2 — 7-day minimum observation)
    ↓
MANUAL LIVE TEST (GATE-3 — human approval required)
    ↓
OPTIONAL AUTOMATED MONITORING (GATE-4 — human approval required)
```

**The system must never transition directly from development to live.**

---

*End of RISK_SPEC.md — Version 2.0*
*GATE-1 APPROVED: 2026-09-21*
