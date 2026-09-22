# TASK-025 Adversarial / Red-Team Review

**Reviewer:** Adversarial Reviewer (Gemini role)
**Task ID:** TASK-025 — Command Correctness and Accounting Observability
**Date:** 2026-09-22

> **Pipeline note:** This review was conducted as part of the CTO review cycle.
> See TASK-023 red-team review for pipeline context.

---

## Summary

TASK-025 fixes command correctness bugs and accounting observability gaps.
Review focused on risk isolation completeness, trade log field integrity, and
potential misuse of the new `risk_override` parameter.

---

## Findings

| Item | Result | Notes |
|---|---|---|
| `risk_override` injection from Telegram | PASS | `risk_override` is only set internally by `handle_market()` — no Telegram command can supply an arbitrary float |
| `_APPROVED_RISK_VALUES` set evaluated at import | PASS | Module-level constant — values are Python floats; `$1.00`, `$2.00` are exactly representable in IEEE 754 |
| Float equality in set membership (`5.0 in _APPROVED_RISK_VALUES`) | PASS | Exact float comparison is safe for these specific values |
| TRADFI list expansion risk | WARN | Adding a new symbol to `config.TRADFI_PERPS` in future automatically changes its TP1 multiplier — this is desired behavior but must be tested when the list changes |
| `log_trade()` writes after lock release | PASS | `log_trade()` operates on `_TRADE_LOG_PATH` — separate file from `active_orders.json`; no `_ORDERS_LOCK` needed |
| Trade log `leverage` type | PASS | Stored as `order.get("leverage")` (int) — not coerced to float; display-safe |
| `/balance` breakdown: missing `tp1`/`tp3`/`notional` in old records | PASS | `_approx_trade_breakdown()` gracefully handles missing fields — breakdown simply does not appear for old records |
| `btc_support` = 0.0 when cache absent | WARN | If `btc_support` is 0.0 (no cache or missing field), card displays `$0.000000 (key zone)`. Low priority — a zero cache means no card is issued anyway (regime check fails first) |
| No injection via `handle_deepdive()` tag | PASS | Tag determined from `config.TRADFI_PERPS` lookup — user-supplied symbol cannot inject an arbitrary tag string |
| Old test assertions replaced correctly | PASS | Two CTO-authorized test updates verified as narrow: only the specific assertions changed, no behavioral test logic altered |

---

## Critical Issues

None that block paper trading.

---

## Recommendations

1. (P2) Add `TRADFI_PERPS` expansion test: when a new symbol is added to the list, a test should confirm its TP1 multiplier is 0.8. Prevents silent misconfiguration.
2. (P3) `btc_support = 0.0` display edge case: add a guard in `send_execution_card_telegram()` — if `btc_support <= 0`, display "N/A" instead of `$0.000000`. Very low risk since card generation already requires a valid regime cache.
3. (P2) Before GATE-3: Audit `TRADFI_PERPS` list completeness. Missing a TRADFI instrument means it gets the CRYPTO TP1 multiplier — oversized first take-profit target.

---

## Release Recommendation

```
APPROVED_WITH_FIXES — approved for paper trading; P2 items recommended before GATE-3
```

---
*Adversarial review: 2026-09-22*
