# STRATEGY_SPEC.md — Bybit Sniper Bot v2.0
# Version: 2.0 | Status: GATE-1 APPROVED | Date: 2026-09-21
# Authority: Human-approved. Protected. Read-only for all agents.
# Do NOT modify without formal Strategy Change Proposal (AGENTS.md Article 6).

---

## 1. SYSTEM OVERVIEW

The Bybit Sniper Bot is a **signal and alert system only**. It identifies
high-conviction momentum setups on Bybit perpetual futures, scores them
by capital flow metrics, calculates S/R-based entry/exit levels, and
delivers formatted execution cards to Telegram. The user executes all
trades manually. The bot places zero orders.

**Runtime:** Python 3.11+ on VPS
**Exchange:** Bybit REST API v5 (read-only market data only)
**Alerts:** Telegram Bot API

---

## 2. BTC REGIME CLASSIFICATION

BTC regime is the master gate. All trade direction is determined by regime.

### 2.1 Primary Regime

```
whale_ratio = longShortRatio (buyRatio / sellRatio from Bybit account-ratio)
fund_side   = "Bullish" if funding_rate >= 0 else "Bearish"

BULLISH:  whale_ratio >= 1.05  AND  fund_side == "Bullish"
BEARISH:  whale_ratio <= 0.95  AND  fund_side == "Bearish"
CHOPPY:   all other conditions
```

### 2.2 Sub-types

```
BULLISH sub-types:
  STRONG:   whale_ratio >= 1.30
  PULLBACK: oi_delta_24h < -2%  (OI declining during BULLISH = shorts covering)
  BREAKOUT: price above both 4H AND 1D resistance levels
  NORMAL:   all other BULLISH

BEARISH sub-types:
  STRONG:   whale_ratio <= 0.77  (inverse of 1.30)
  NORMAL:   all other BEARISH
```

### 2.3 Regime → Direction

```
BULLISH → LONG setups eligible
BEARISH → SHORT setups eligible
CHOPPY  → NO TRADES (observation only)
```

### 2.4 Regime Warning Flags

```
CROWDED:         abs(funding_rate) > 0.0005  → reduce size
HIGH_VOLATILITY: price_range_24h_pct > 8%    → tighten stops
EXTREME_GREED:   fear_greed_index >= 80      → reduce size
```

### 2.5 BTC Regime Thresholds (IMMUTABLE)

```python
BTC_BULL_WHALE_MIN  = 1.05
BTC_BEAR_WHALE_MAX  = 0.95
BTC_STRONG_MIN      = 1.30
BTC_INVALIDATION    = 84200   # update each session from S/R data
```

---

## 3. COIN UNIVERSE

### 3.1 Dynamic Universe (Screener — every research cycle)

**Gainers (LONG setups — BULLISH regime only):**
```
price24hPcnt > 8%  AND  < 50%
turnover24h >= $1,000,000
not in PERMANENT_SKIP_LIST
not in SESSION_SKIP_LIST
```

**Losers (Reversal LONG setups — BULLISH regime only):**
```
price24hPcnt < -10%
turnover24h >= $1,000,000
not in PERMANENT_SKIP_LIST
```

**Quick Scan universe (HIGH_CONVICTION threshold — every 30 minutes):**
```
price24hPcnt > 8%  AND  < 40%
turnover24h >= $5,000,000
whale_ratio >= 2.5  AND  fund_side == "Bullish"
```

### 3.2 Permanent Skip List (IMMUTABLE)

These symbols are permanently excluded from all scanning:
```python
PERMANENT_SKIP_LIST = [
    "MARSCOIN",   # liquidation magnet, 2x liquidation events
    "LONGXIA",    # persistent whale short every session
    "GRAM",       # persistent whale short every session
    "DGAI",       # no reliable capital flow data
    "BR",         # token unlock confirmed
    "AKE",        # recurring large unlock events
    "VELVET",     # OI +621% parabolic, funding 10x normal
    "MELANIA",    # OI declining -46%, stale whale signal
    "SOSO",       # recurring unlock events
    "ZEC",        # exchange exit event, manipulation risk
]
```

### 3.3 TradFi Perpetuals (IMMUTABLE)

Scanned every research cycle with higher whale bar (>= 2.0):
```python
TRADFI_PERPS = [
    "COINUSDT",   # Coinbase stock perp — highest signal quality
    "MSTRUSDT",   # MicroStrategy — BTC proxy amplifier
    "XAUUSDT",    # Gold — most reliable weekend, safe haven
    "NVDAUSDT",   # Nvidia — scan only when semis had big move
]
TRADFI_MIN_WHALE_RATIO = 2.0
```

TradFi TP multipliers differ from crypto (see Section 6.2).

### 3.4 Standing Watchlist (IMMUTABLE — qualification rules)

```python
WATCHLIST_STANDING = {
    "LINKUSDT": {
        "qualify_if": "whale_ratio > 1.3 AND fund_side == Bullish",
        "skip_if":    "fund_side == Bearish OR whale_ratio <= 1.0",
        "edge":       "BTC mirror — amplifies BTC moves 1.2-1.8x"
    },
    "LABUSDT": {
        "qualify_if": "whale_ratio >= 3.0 AND fund_side == Bullish",
        "skip_if":    "whale_ratio < 2.0",
        "edge":       "small cap momentum, high B/S ratio"
    },
    "BEATUSDT": {
        "qualify_if": "whale_ratio >= 4.0 AND price_change_24h < 5",
        "skip_if":    "whale_ratio < 3.0",
        "edge":       "smart money accumulation pattern"
    },
    "TAOUSDT": {
        "qualify_if": "whale_ratio >= 2.0 AND top_trader_ratio >= 2.0",
        "skip_if":    "price already ran >8% on the day",
        "edge":       "AI narrative + institutional backing"
    },
    "WIFUSDT": {
        "qualify_if": "whale_ratio >= 2.0 AND fund_side == Bullish",
        "skip_if":    "daily_change > 15%",
        "edge":       "negative funding = shorts pay longs = squeeze"
    },
    "ONDOUSDT": {
        "qualify_if": "whale_ratio >= 1.3 AND fund_side == Bullish",
        "skip_if":    "whale_ratio < 1.1",
        "edge":       "RWA narrative — SEC tokenized securities"
    },
    "MNTUSDT": {
        "qualify_if": "whale_ratio >= 2.5 AND funding < 0.008",
        "skip_if":    "funding >= 0.01",
        "edge":       "top trader ratio often exceeds whale ratio"
    },
}
```

---

## 4. COIN SCORING SYSTEM (IMMUTABLE)

All scoring is deterministic Python. LLM is NOT used for scoring.

```python
def score_coin(symbol, flow, news_result, tag) -> int:
    score = 0

    # Whale ratio
    whale = flow["longShortRatio"]
    if   whale >= 9.0: score += 60   # extraordinary (COIN-level)
    elif whale >= 5.0: score += 50   # exceptional
    elif whale >= 3.0: score += 40
    elif whale >= 2.0: score += 30
    elif whale >= 1.5: score += 20
    else:              score += 5

    # Fund side
    if flow["fundSide"] == "Bullish":  score += 20
    if flow["fundSide"] == "Bearish":  score -= 30

    # Top trader ratio
    top_trader = flow["topTraderPositionRate"]
    if   top_trader >= 2.0: score += 15
    elif top_trader >= 1.5: score += 8

    # Funding rate health
    funding = abs(flow["fundingRate"]["latest"])
    if funding <= 0.0001: score += 10   # healthy
    if funding >= 0.0005: score -= 15   # overcrowded

    # OI trend
    oi_now = flow["openInterestHistory"]["current"]
    oi_30d = flow["openInterestHistory"]["thirtyDaysAgo"]
    if oi_30d:
        oi_growth = (oi_now - oi_30d) / oi_30d
        if oi_growth >  0.05: score += 10
        if oi_growth < -0.10: score -= 20

    # News
    if news_result:
        if news_result.get("sentiment") == "BULLISH": score += 10
        if news_result.get("sentiment") == "BEARISH": score -= 20
        if news_result.get("unlock_today"):           score = -999
        if news_result.get("exploit_today"):          score = -999

    # Permanent skip
    if symbol.replace("USDT","") in PERMANENT_SKIP_LIST: score = -999

    # TradFi discount
    if tag == "TRADFI": score = max(0, score - 10)

    return score
```

---

## 5. ENTRY RULES

### 5.1 S/R Zone Entry

Entry is ONLY permitted within a confirmed S/R zone:
- Entry zone calculated by confluence of 3 methods:
  Bollinger Bands (5M, 4H, 1D) + Swing Highs/Lows (5M, 4H, 1D) + Volume Profile (5M)
- Cluster tolerance: 0.5% (levels within 0.5% = same zone)
- Entry mid = midpoint of strongest confluence cluster below current price

### 5.2 Dead Cat Filter — 3 Rules (ALL MUST PASS)

Before any entry, the following 3 rules must pass on the most recent
completed 5M candle:

```
Rule 1 — Midpoint close:
  candle.close >= (candle.high + candle.low) / 2
  (Buyers absorbed selling — real support)

Rule 2 — Volume participation:
  candle.volume >= 0.70 * mean(prior_3_candles_volume)
  (Real participation, not thin air)

Rule 3 — Higher low:
  candle.low > prior_candle.low
  (Confirmed support holding)

RESULT:
  ALL 3 pass → "ENTRY CONFIRMED" — safe to place limit order
  ANY fail   → "DEAD CAT WARNING" — wait for next 5M candle
```

### 5.3 Pre-Entry Checklist (User verifies visually)

```
□ 15M candle CLOSED ≥ zone_bottom
□ 5M candle GREEN with volume
□ Candle closes ABOVE its midpoint
□ Volume ≥ 70% of prior 3 candles
□ BTC holding above btc_support level
□ No negative news in last 5 minutes
❌ Any box fails → wait next candle
```

### 5.4 Validity Windows (IMMUTABLE)

```
gap_pct = (current_price - entry_zone_top) / current_price * 100

gap_pct < 1%:   URGENT     — 10 minutes
gap_pct 1-3%:   PATIENT    — 30 minutes
gap_pct > 3%:   SET&FORGET — 60 minutes

Cancel rule: if price moves > 3% past zone unfilled → cancel
```

---

## 6. STOP LOSS AND TAKE PROFIT

### 6.1 Stop Loss

```
SL = deepest confirmed support cluster below entry_zone_bottom + 0.3% buffer

BTC Invalidation level: if BTC loses BTC_INVALIDATION → exit all positions
```

### 6.2 Take Profit — Two-Layer Structure (IMMUTABLE)

```
CORE (50% of position) — NO EXCEPTIONS:
  LONG  (crypto): TP1 = entry + (entry × stop_dist_pct × 1.0)
  LONG  (TradFi): TP1 = entry + (entry × stop_dist_pct × 0.8)
  SHORT (crypto): TP1 = entry - (entry × stop_dist_pct × 1.0)
  SHORT (TradFi): TP1 = entry - (entry × stop_dist_pct × 0.8)

RUNNER (50% of position) — after TP1:
  TP2 = entry ± (entry × stop_dist_pct × 1.5)
  TP3 = entry ± (entry × stop_dist_pct × 2.5)

Post-TP1 action:
  Close CORE (50%) immediately
  Move SL to entry (breakeven)
  Hold RUNNER targeting TP2 then TP3
```

---

## 7. TIME RULES (IMMUTABLE)

```
Time-stop:  2 hours from fill with no TP1 hit → close ALL
Hard close: 20:00 UTC daily → close ALL positions
```

---

## 8. LLM USAGE POLICY

```
ALLOWED:
  ✅ BTC news sentiment (3 headlines → BULLISH/BEARISH/NEUTRAL)
  ✅ Coin-specific news (unlock/hack/delisting detection)

FORBIDDEN:
  ❌ Scoring
  ❌ Position sizing
  ❌ S/R calculation
  ❌ Regime classification
  ❌ TP/SL calculation
  ❌ Any trading math

Fallback: if LLM unavailable → continue with neutral news (no crash)
```

### 8.1 OpenRouter Models (IMMUTABLE order)

```python
OPENROUTER_MODELS = [
    "qwen/qwen-2.5-7b-instruct",      # first choice
    "google/gemma-2-9b-it",           # second choice
    "mistralai/mistral-7b-instruct",  # third choice
]
OPENROUTER_MAX_PROMPT_TOKENS   = 800
OPENROUTER_MAX_RESPONSE_TOKENS = 400
```

---

## 9. SCHEDULER

```
Research (full pipeline):  every 4 hours
Monitor (position check):  every 2 minutes (only if active_orders non-empty)
BTC check (regime only):   every 15 minutes
Quick scan (screener only): every 30 minutes
```

---

## 10. EXTREME EVENTS — HALT RESEARCH

The following events trigger a research halt (no chains to planning):
```
FOMC / Fed rate decision
CPI / PPI / PCE / NFP
SEC crypto ruling
Bitcoin ETF approval/rejection
Major exchange hack/collapse
Deribit quarterly expiry day
```

---

*End of STRATEGY_SPEC.md — Version 2.0*
*GATE-1 APPROVED: 2026-09-21*
*Next review: after 7-day paper trading observation (GATE-2)*
