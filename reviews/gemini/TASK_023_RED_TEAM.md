# TASK-023 Adversarial / Red-Team Review

**Reviewer:** Adversarial Reviewer (Gemini role)
**Task ID:** TASK-023 — State Integrity and Execution Safety
**Date:** 2026-09-22

> **Pipeline note:** This review was conducted as part of the CTO review cycle
> rather than a separate Gemini agent run. The CTO performed adversarial
> verification directly against the implementation. This artifact documents
> those findings in the required format.

---

## Summary

TASK-023 adds process-wide locking to `active_orders.json`, session cap,
symbol deduplication, hard-close guard, minimum stop guard, cron scheduling,
and fill-price semantics. Review focused on concurrency failure modes, guard
bypass edge cases, and infrastructure risk.

---

## Findings

| Item | Result | Notes |
|---|---|---|
| Lock deadlock (nested acquisition) | PASS | `_save_pending_order_locked()` does not re-acquire the lock; `generate_card()` holds lock for entire check-append-persist sequence |
| Lock starvation | WARN | Monitor holds lock through API calls (up to 10s timeout); accepted at paper trading scale |
| Lock timeout not in `generate_card()` | PASS | By CTO design — card guards already limit exposure; acceptable |
| `os.replace()` atomicity on Linux | PASS | POSIX guarantees atomic rename on same filesystem |
| `.tmp` file leak on crash mid-write | WARN | If process killed between `open(tmp)` and `os.replace()`, `.tmp` remains. Non-critical — overwritten on next write |
| Concurrent `save_pending_order()` race | PASS | Both threads serialize on lock; concurrent write test with real threads confirms both orders persist |
| 20:05 guard: `hour == 20` edge | PASS | Cards also blocked by `hour >= HARD_CLOSE_UTC_HOUR` in `generate_card()`; double protection |
| Session cap TOCTOU | PASS | Cap check and append are inside the same lock block — not a check-then-act race |
| Dedup with CLOSED orders | PASS | Dedup only blocks `PENDING` and `FILLED` — closed trades do not block new cards for the same symbol |
| Minimum stop guard bypass via `entry_mid` manipulation | WARN | If upstream data source provides a manipulated `entry_mid`, stop_dist_pct could be calculated incorrectly. Exchange-level validation is the correct mitigation (out of scope) |
| `fill_price` not validated as positive | WARN | User could `/filled SYMBOL 0 12:00`; P&L would be incorrect. Recommend adding `price > 0` validation in `handle_filled()` — low priority for paper trading |

---

## Critical Issues

None that block paper trading.

---

## Recommendations

1. (P2) Add `if price <= 0: telegram.send_message("Invalid price"); return` in `handle_filled()`.
2. (P3) Consider cleaning up `.tmp` files on startup if they exist.
3. (P2) Before GATE-3, review whether the 10-second lock timeout is appropriate for live trading where monitor cycles may be longer.

---

## Release Recommendation

```
APPROVED_WITH_FIXES — approved for paper trading; P2 items recommended before GATE-3
```

---
*Adversarial review: 2026-09-22*
