# TASK-025 Quantitative / Strategy Review

**Reviewer:** Quantitative Auditor (Sonnet role)
**Task ID:** TASK-025 — Command Correctness and Accounting Observability
**Date:** 2026-09-22

> **Pipeline note:** This review was conducted as part of the CTO review cycle.
> See TASK-023 quant review for pipeline context.

---

## Summary

TASK-025 fixes TRADFI tag detection, eliminates the global risk mutation in
`/market`, enriches the trade log, clarifies the balance label, and relabels
`btc_support` display. Review focused on TP multiplier correctness, risk
isolation, P&L accounting integrity, and trade log field accuracy.

---

## Findings

| Item | Result | Notes |
|---|---|---|
| Strategy parameters unchanged | PASS | No entry logic, stop rules, or TP ratios modified |
| TRADFI TP1 multiplier (0.8×) | PASS | `multiplier = 0.8 if tag == "TRADFI" else 1.0` in `generate_card()` — applied to TP1 only, TP2/TP3 unchanged |
| TRADFI source of truth | PASS | `config.TRADFI_PERPS` list — single definition, no duplication |
| `risk_override` approved values | PASS | `{PAPER_RISK_PER_TRADE, PAPER_RISK_CAUTION, PAPER_RISK_MARKET}` — only pre-approved dollar values accepted |
| `risk_override=None` default behavior | PASS | Defaults to `PAPER_RISK_PER_TRADE` — all existing callers unaffected |
| Global `PAPER_RISK_PER_TRADE` immutability | PASS | Verified by `test_generate_card_no_global_mutation` — global unchanged after `/market` call |
| Trade log: fill_price accuracy | PASS | `fill_price` = user-supplied value from `/filled`; falls back to `entry` if absent (backwards compatible) |
| Trade log: tp1/tp3/notional | PASS | All sourced from the card dict, which is computed deterministically from config constants and S/R levels |
| Balance breakdown label | PASS | "(estimated)" appended — operator cannot mistake projection for settled P&L |
| `btc_support` = `entry_zone_bottom` | PASS (Option B) | Honest relabelling to "key zone" — value is structurally below current BTC price as verified by test |
| No look-ahead bias | PASS | All new fields sourced from same-timestamp card data |

---

## Critical Issues

None.

---

## Recommendations

1. `btc_support` precision (TASK-026 candidate): `entry_zone_bottom` is the best available near-support proxy given the current S/R interface. Flag for improvement if true lowest support becomes a strategy requirement before GATE-3.
2. The `/balance` core/runner breakdown is an approximation from logged `tp1`/`tp3` fields. Document clearly in operator guide that it is not a verified settlement figure.

---

## Release Recommendation

```
APPROVED — no quant concerns for paper trading phase
```

---
*Quant review: 2026-09-22*
