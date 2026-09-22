# STRATEGY CHANGE PROPOSAL 001 â€” BTC Invalidation Level

**ID:** SCP-001
**Submitted:** 2026-09-22
**Submitted by:** Lead CTO
**Status:** PENDING HUMAN APPROVAL

---

## Current Rule (verbatim, from `bybit_bot/config.py`)

```python
BTC_INVALIDATION = 84200
```

Displayed on every execution card as:

```
â˜ ï¸ BTC loses $84,200 â†’ exit
```

## Problem

`BTC_INVALIDATION = 84200` is a fixed dollar level set at strategy creation
time. As BTC's price moves during the observation period and beyond, this
constant becomes:

- **Too high (above current BTC price):** The invalidation warning would
  never trigger, giving false confidence.
- **Too low (far below current BTC price):** The warning triggers too late,
  after significant adverse movement has already occurred.
- **Ambiguous intent:** "$84,200" reads as a support level, but as the market
  moves it no longer corresponds to any meaningful S/R zone.

## Proposed Rule

Replace the fixed dollar constant with a percentage-based invalidation
calculated at card generation time:

**Proposed formula:**
```
btc_invalidation_price = round(btc_price_at_card_time Ã— 0.95, 0)
```

Displayed on card as:
```
â˜ ï¸ BTC drops 5% to $XX,XXX â†’ exit
```

The 5% threshold represents a meaningful intraday regime shift â€” consistent
with the BTC_BULL_WHALE_MIN and BTC_BEAR_WHALE_MAX thresholds already in
the strategy.

## Reason for Change

The current hardcoded value has no mechanism for updating as market conditions
change. A relative threshold (5% from entry-time BTC price) is self-adjusting
and always contextually meaningful regardless of BTC price level.

## Supporting Evidence

- The existing `BTC_BULL_WHALE_MIN = 1.05` and `BTC_BEAR_WHALE_MAX = 0.95`
  already use relative thresholds for regime classification.
- A 5% BTC decline in a 2H trading window is consistent with the strategy's
  "regime shift" definition.
- No backtesting data available yet (GATE-2 in progress). Backtest validation
  required before approval.

## Out-of-Sample Validation

Not yet available. Must be completed during 7-day paper observation period
and reported before human approval.

## Expected Risks and Failure Modes

- **Too tight (5%):** Normal BTC volatility could trigger false invalidations.
  Consider 7% if paper trading shows excessive false positives.
- **Execution:** Requires adding `btc_invalidation` as a computed card field
  in `execution.generate_card()` and removing `BTC_INVALIDATION` from display
  (the constant remains in config as the default for non-card uses).

## Code Impact (if approved)

- `bybit_bot/execution.py` â€” compute `btc_invalidation_price` at card time
- `bybit_bot/config.py` â€” `BTC_INVALIDATION` constant deprecated for display;
  retained as fallback for monitor checks if needed
- Display line updated on execution card
- Test: `test_btc_invalidation_is_5pct_below_card_btc_price`

## Recommendation

**CTO recommends APPROVAL after 7-day paper observation.**

Do not implement before:
1. Paper trading observation complete
2. Sonnet quant review confirms 5% threshold is not too tight
3. Gemini adversarial review confirms no edge case failures
4. Human signs off

## Approval Decision

```
[ ] APPROVED â€” implement as specified
[ ] APPROVED WITH MODIFICATION â€” specify: _______________
[ ] REJECTED â€” reason: _______________
[ ] DEFERRED â€” revisit after: _______________
```

Human signature / timestamp: _______________
