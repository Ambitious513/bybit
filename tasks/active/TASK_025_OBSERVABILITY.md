# TASK-025 — Command Correctness and Accounting Observability

## 1. Objective

Fix TRADFI tagging bugs in `/deepdive` and `/market`, eliminate the
thread-unsafe global risk mutation in `/market`, enrich the trade log
so `/balance` breakdown is accurate, and correct or relabel `btc_support`.

## 2. Background

The CTO + Codex joint review (2026-09-22) identified:
- `/deepdive` and `/market` both hardcode `tag="CRYPTO"`, producing the wrong
  TP1 multiplier (1.0x instead of 0.8x) for TRADFI perps (gold, NVDA, etc.).
- `handle_market()` temporarily mutates the module-global
  `execution.PAPER_RISK_PER_TRADE`. A concurrent `generate_card()` call from
  the scheduler could inherit the $1.00 market risk instead of $2.00.
- `monitor.log_trade()` omits `tp1`, `tp3`, and `notional` — so
  `/balance`'s core/runner breakdown never appears for any logged trade.
- `btc_support` in the research cache stores `entry_zone_bottom` (the bottom
  of the best selected entry zone), which is not necessarily BTC's lowest
  confirmed support level. This is displayed on every execution card,
  misleading the operator.

## 3. Source-of-Truth Documents

- `bybit_bot/config.py` — `TRADFI_PERPS` list (read-only)
- `docs/STRATEGY_SPEC.md` (do NOT modify)
- `docs/RISK_SPEC.md` (do NOT modify)
- `AGENTS.md` v2.0

## 4. Scope

Command correctness, risk isolation, trade-log enrichment, and btc_support
accuracy. No strategy parameters modified.

## 5. Allowed Files / Directories

- `bybit_bot/orchestrator.py`
- `bybit_bot/execution.py`
- `bybit_bot/monitor.py`
- `bybit_bot/research.py` — `btc_support` derivation only
- `bybit_bot/tests/test_observability.py` — NEW file

## 6. Forbidden Files / Directories

- `bybit_bot/config.py`
- `bybit_bot/sr_calculator.py`
- `bybit_bot/planning.py`
- `bybit_bot/screener.py`
- `bybit_bot/quickscan.py`
- `docs/STRATEGY_SPEC.md`
- `docs/RISK_SPEC.md`
- `AGENTS.md`
- All existing test files

## 7. Requirements

### R1 — TRADFI Tag Detection in `/deepdive` and `/market`

In both `handle_deepdive()` and `handle_market()` in `orchestrator.py`:

```python
from bybit_bot.config import TRADFI_PERPS
tag = "TRADFI" if symbol in TRADFI_PERPS else "CRYPTO"
```

Pass this `tag` into the `setup` dict so `execution.generate_card()` uses
the correct TP1 multiplier.

Do NOT create any new class or data structure. `TRADFI_PERPS` in `config.py`
is the definitive source of truth.

### R2 — `/market` Risk Isolation via Parameter

Add an optional parameter to `execution.generate_card()`:

```python
def generate_card(
    setup: dict,
    risk_override: float | None = None,
) -> dict | None:
```

Inside `generate_card()`, determine the effective risk:

```python
from bybit_bot.config import (
    PAPER_RISK_CAUTION, PAPER_RISK_MARKET, PAPER_RISK_PER_TRADE
)
_APPROVED_RISK_VALUES = {PAPER_RISK_PER_TRADE, PAPER_RISK_CAUTION, PAPER_RISK_MARKET}

if risk_override is not None:
    if risk_override not in _APPROVED_RISK_VALUES:
        logger.error(
            "generate_card_invalid_risk_override value=%.2f symbol=%s",
            risk_override, setup.get("symbol"),
        )
        return None
    risk = risk_override
else:
    risk = PAPER_RISK_PER_TRADE
```

Update `handle_market()` to pass the override instead of mutating the global:

```python
card = execution.generate_card(setup, risk_override=PAPER_RISK_MARKET)
```

Remove the `original_risk` save/restore block entirely.

### R3 — Enrich `log_trade()` Record

In `monitor.log_trade()`, add the following fields to every closed trade
record (in addition to the existing fields):

```python
"planned_entry": _float(order.get("planned_entry", order.get("entry"))),
"fill_price":    _float(order.get("fill_price", order.get("entry"))),
"sl":            _float(order.get("sl")),
"tp1":           _float(order.get("tp1")),
"tp3":           _float(order.get("tp3")),
"notional":      _float(order.get("notional")),
"leverage":      order.get("leverage"),
"stop_dist_pct": _float(order.get("stop_dist_pct")),
```

All fields use safe `_float()` helpers. Missing fields silently default to 0.0.
This enrichment is additive and fully backwards-compatible with existing records.

### R4 — `/balance` Breakdown Label Clarity

In `orchestrator._approx_trade_breakdown()` and its display in
`handle_balance()`, ensure the label is clearly approximate:

```python
message += (
    f"\nApprox core at TP1: ${breakdown[0]:+.2f}"
    f" | runner at TP3: ${breakdown[1]:+.2f} (estimated)"
)
```

Do NOT change the balance accounting logic. Do NOT change `update_paper_balance()`.

### R5 — `btc_support` Accuracy or Honest Relabelling

In `research.py`, where `btc_support` is derived from the BTC S/R calculation,
implement Option A. Fall back to Option B only with explicit justification.

**Option A (preferred):** Calculate `btc_support` as the minimum support level
returned by `sr_calculator.get_sr_levels("BTCUSDT", btc_price)` that is
strictly below the current BTC price:

```python
btc_sr = sr_calculator.get_sr_levels("BTCUSDT", btc_price)
# Collect all candidate support levels below current price
candidates = [
    v for k, v in btc_sr.items()
    if isinstance(v, (int, float)) and 0 < v < btc_price
]
btc_support = min(candidates) if candidates else 0.0
```

**Option B (fallback):** If Option A produces unreliable or zero values in
testing, keep the current `entry_zone_bottom` value but rename the field in
the execution card display from `btc_support` to `btc_key_zone` and update
the card line:

```python
f"║  □ BTC holding above ${_price(_float(card.get('btc_support')))} (key zone)"
```

Codex must attempt Option A first. If tests show Option A returns 0.0 for
valid BTC prices more than 20% of the time in simulated calls, use Option B
and document the fallback explicitly in the completion report.

### R6 — Existing Tests Must Pass

Full suite including T023 and T024 tests. Zero regressions permitted.

## 9. Interfaces / Contracts

### `generate_card()` Signature Change

```python
def generate_card(
    setup: dict,
    risk_override: float | None = None,
) -> dict | None:
```

Default `None` preserves existing behaviour for all callers (planning, deepdive,
quickscan deepdive path). Only `handle_market()` passes a value.

### Approved risk values

```python
_APPROVED_RISK_VALUES = {2.00, 1.00}  # PAPER_RISK_PER_TRADE, PAPER_RISK_CAUTION/MARKET
```

### Trade log enriched schema (additive)

New fields added to every record written after this task:
`planned_entry`, `fill_price`, `sl`, `tp1`, `tp3`, `notional`, `leverage`, `stop_dist_pct`

## 10. Acceptance Criteria

- All R1–R5 implemented
- Full test suite passes (all existing + T023 + T024 + new T025 tests)
- No config.py, STRATEGY_SPEC.md, or RISK_SPEC.md changes
- `generate_card()` signature is backwards-compatible (risk_override defaults to None)
- btc_support either uses lowest confirmed support or is honestly relabelled

## 11. Required Tests

Create `bybit_bot/tests/test_observability.py` with at minimum:

1. `test_deepdive_tradfi_symbol_gets_tradfi_tag` — XAUUSDT → tag="TRADFI" in setup
2. `test_deepdive_crypto_symbol_gets_crypto_tag` — WIFUSDT → tag="CRYPTO" in setup
3. `test_market_tradfi_symbol_gets_tradfi_tag` — NVDAUSDT → tag="TRADFI" in setup
4. `test_market_risk_override_uses_approved_value` — PAPER_RISK_MARKET passed correctly
5. `test_generate_card_rejects_invalid_risk_override` — value=5.00 → returns None
6. `test_generate_card_no_global_mutation` — after handle_market() call, verify
   `execution.PAPER_RISK_PER_TRADE` is still 2.00 (global unchanged)
7. `test_log_trade_includes_tp_fields` — closed trade record contains tp1, tp3, notional
8. `test_log_trade_includes_fill_price` — fill_price present in trade log record
9. `test_balance_breakdown_label_includes_estimated` — balance message contains
   "estimated" in breakdown line
10. `test_btc_support_below_current_btc_price` — btc_support value < mock BTC price

## 12. Expected Deliverables

- Modified: `bybit_bot/orchestrator.py`
- Modified: `bybit_bot/execution.py`
- Modified: `bybit_bot/monitor.py`
- Modified: `bybit_bot/research.py` (btc_support only)
- New: `bybit_bot/tests/test_observability.py`
- Completion report (Article 9 format)
- If Option B used for btc_support: written justification in completion report

## 13. Failure / Escalation Conditions

STOP and escalate if:
- `risk_override` validation requires changing `PAPER_RISK_PER_TRADE` values
  in config.py (GATE-1 locked)
- `get_sr_levels()` for BTCUSDT raises consistently in integration testing
- btc_support Option A returns 0.0 for more than 20% of valid test cases

## 14. Completion Report Requirements

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list with line ranges]
btc_support: [Option A or Option B — with justification if Option B]
Tests Run: [pytest output summary]
Tests Passed: [count]
Tests Failed: [count + names + errors]
Known Issues: [unresolved]
Remaining Risks: [identified but unaddressed]
Recommended Next Step: Full paper trading observation + Sonnet/Gemini audit
```

## 15. Review Plan

```
Codex implements → Sonnet quant review → Gemini adversarial → CTO final review
```

## 16. Skill Extraction Decision

No skill to be extracted.

## 17. Status / Sign-off

```
Status: READY FOR IMPLEMENTATION (begin after TASK-024 approved)
Assigned to: Codex
CTO sign-off: 2026-09-22
Gate dependency: Must complete before GATE-3 decision
```
