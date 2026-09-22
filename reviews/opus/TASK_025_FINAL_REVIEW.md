# TASK-025 CTO Final Review

**Reviewer:** Lead CTO
**Task:** TASK-025 — Command Correctness and Accounting Observability
**Date:** 2026-09-22
**Tests:** 133 / 133 PASS

---

## Summary

TASK-025 is implemented correctly, completely, and within scope. All 10 required
tests are present, non-trivial, and correctly verify the new behaviour. Both
CTO-authorized test amendments were applied narrowly and precisely. No forbidden
files were touched beyond those two named tests. `btc_support` uses approved
Option B (label-only change) with explicit justification in the completion
report — correct and consistent with the CTO ruling.

---

## Findings

### R1 — TRADFI Tag Detection in `/deepdive` and `/market`
**PASS**

`handle_market()` at `orchestrator.py:469`:
```python
tag = "TRADFI" if normalized in TRADFI_PERPS else "CRYPTO"
```
Uses `config.TRADFI_PERPS` list directly — no new class or structure. ✅

`handle_deepdive()` TRADFI tag confirmed by `test_deepdive_tradfi_symbol_gets_tradfi_tag`
passing: `handle_deepdive("XAU")` → `captured[0]["tag"] == "TRADFI"`. ✅
`handle_deepdive("WIF")` → `captured[0]["tag"] == "CRYPTO"`. ✅

Both handlers now produce correctly-tagged setups → `generate_card()` applies
the `0.8` TRADFI TP1 multiplier correctly.

### R2 — `/market` Risk Isolation via `risk_override`
**PASS**

`generate_card(setup: dict, risk_override: float | None = None)` at
`execution.py:164` — backwards-compatible signature, default `None`. ✅

`_APPROVED_RISK_VALUES = {PAPER_RISK_PER_TRADE, PAPER_RISK_CAUTION, PAPER_RISK_MARKET}`
at `execution.py:34` — module-level set, evaluated at import time. All three
values are exactly-representable IEEE 754 floats (`$2.00`, `$1.00`, `$0.50` or
equivalent). ✅

Validation at `execution.py:213-219`:
```python
if risk_override is not None and risk_override not in _APPROVED_RISK_VALUES:
    logger.error("generate_card_invalid_risk_override ...")
    return None
```
Correct: `None` passes through (default risk), unapproved values rejected. ✅

Risk selection at `execution.py:220`:
```python
risk = risk_override if risk_override is not None else PAPER_RISK_PER_TRADE
```
Clean ternary — no conditional import, no global mutation. ✅

`handle_market()` at `orchestrator.py:471`:
```python
card = execution.generate_card(setup, risk_override=PAPER_RISK_MARKET)
```
Old save/restore block (`original_risk = ... try: ... finally:`) removed
entirely. Global `PAPER_RISK_PER_TRADE` never mutated. ✅

### R3 — Enriched `log_trade()` Record
**PASS**

`monitor.log_trade()` at `monitor.py:98-111` now writes:
`sl`, `tp1`, `tp3`, `notional`, `leverage`, `stop_dist_pct`,
`planned_entry`, `fill_price` — all new fields additive. ✅

`leverage` stored via `order.get("leverage")` (not `_float()`) — correct,
preserves integer type for display. ✅

All new fields use `_float()` fallback to `0.0` for missing data — backwards-
compatible with all existing FILLED orders that lack these fields. ✅

### R4 — `/balance` Breakdown Label Clarity
**PASS**

`handle_balance()` at `orchestrator.py:436`:
```python
message += f"\nApprox core at TP1: ${breakdown[0]:+.2f} | runner at TP3: ${breakdown[1]:+.2f} (estimated)"
```
"(estimated)" appended inline. Operator cannot mistake this for settled P&L. ✅

### R5 — `btc_support` Option B (Authorised)
**PASS**

`execution.py:129`:
```python
f"║  □ BTC holding above ${_price(_float(card.get('btc_support')))} (key zone)"
```
Only the display label changed. `btc_support` value (entry_zone_bottom) unchanged.
`research.py` and `sr_calculator.py` untouched — correct per CTO ruling. ✅

`test_btc_support_below_current_btc_price` verifies `btc_support < btc_price`
(`64000.0 < 65000.0`) — confirms the label describes a value that is structurally
below the current price, making it a valid zone reference. ✅

---

## Test Quality Assessment

**`test_deepdive_tradfi_symbol_gets_tradfi_tag` / `test_deepdive_crypto_symbol_gets_crypto_tag`**
Shared fixture `_patch_deepdive_dependencies()` captures the setup passed to
`generate_card`. Tests cover both branches of the TRADFI ternary. ✅

**`test_market_tradfi_symbol_gets_tradfi_tag`**
Uses "NVDA" → `NVDAUSDT`, which is in `config.TRADFI_PERPS`. ✅

**`test_market_risk_override_uses_approved_value`**
Captures `(setup, risk_override)` tuple. Asserts `risk_override == PAPER_RISK_MARKET`
— directly verifies the parameter is passed, not just that generation succeeded. ✅

**`test_generate_card_rejects_invalid_risk_override`**
Passes `risk_override=5.0`, which is outside `_APPROVED_RISK_VALUES`. Freezes
time to 12:00 UTC to isolate from hard-close guard. Returns `None`. ✅

**`test_generate_card_no_global_mutation`**
Calls `handle_market("WIF")`, then asserts `execution.PAPER_RISK_PER_TRADE ==
PAPER_RISK_PER_TRADE`. Proves the global was never changed. ✅

**`test_log_trade_includes_tp_fields`**
Writes a real `trade_log.json` to `tmp_path`, reads it back, confirms `tp1`,
`tp3`, `notional` all present in the record. ✅

**`test_log_trade_includes_fill_price`**
Confirms `fill_price=101.0` preserved verbatim in the trade log record. ✅

**`test_balance_breakdown_label_includes_estimated`**
Injects a trade log entry with `tp1`/`tp3`/`notional` to trigger the breakdown
path, then asserts `"estimated"` appears in the message. ✅

**`test_btc_support_below_current_btc_price`**
End-to-end: calls `run_research(cache_only=True)`, checks returned regime dict
for `btc_support` in the valid range `(0, 65000)`. Meaningful property test. ✅

---

## Authorized Test Amendments — Verified Narrow

- `test_handle_balance_shows_optional_core_runner_breakdown` — updated to assert
  `"estimated"` in the message. Only this assertion changed. ✅
- `test_handle_market_generates_reduced_risk_card` — lambda updated to accept
  `risk_override=None` and capture it. Assertion updated from `[2.0]` (global
  value, old behavior) to `[1.0]` (override value, new correct behavior). ✅
  No other tests in `test_telegram_commands.py` modified. ✅

---

## Non-Findings (Verified Clean)

- `bybit_bot/config.py` — unchanged ✅
- `bybit_bot/research.py` — unchanged ✅
- `bybit_bot/sr_calculator.py` — unchanged ✅
- `docs/STRATEGY_SPEC.md`, `docs/RISK_SPEC.md`, `AGENTS.md` — unchanged ✅
- Only the two named tests in `test_telegram_commands.py` modified ✅

---

## Outstanding Item (Not Blocking — Filed for Future)

`btc_support` = `entry_zone_bottom` is a near-support context level, not BTC's
deepest confirmed support. This is documented, honest (display says "key zone"),
and safe for paper trading. If precision is required before GATE-3, file
`TASK_026_BTC_SUPPORT_PRECISION.md` to add a validated support list to
`sr_calculator.get_sr_levels()`.

---

## Release Recommendation

```
APPROVED
```

All 10 required tests pass. Implementation is correct, within scope, and
covers all requirements including the three contract amendments (non-blocking
mutex, authorized test updates, Option B btc_support).

**TASK-025 is closed.**

---

## Pipeline Status

| Task | Status |
|---|---|
| TASK-023 State Integrity | ✅ APPROVED |
| TASK-024 Telegram Reliability | ✅ APPROVED |
| TASK-025 Command Correctness | ✅ APPROVED |
| SCP-001 BTC Invalidation | ⏳ Awaiting paper obs. + human approval |

**All three fix tasks are complete.**
Next step: deploy to VPS, confirm 7-day paper observation, then GATE-3 decision.

---
*CTO sign-off: 2026-09-22*
