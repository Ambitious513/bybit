# TASK-024 Quantitative / Strategy Review

**Reviewer:** Quantitative Auditor (Sonnet role)
**Task ID:** TASK-024 — Telegram Reliability and Research Scheduling
**Date:** 2026-09-22

> **Pipeline note:** This review was conducted as part of the CTO review cycle.
> See TASK-023 quant review for pipeline context.

---

## Summary

TASK-024 introduces durable Telegram offset persistence, per-update exception
isolation, poll loop backoff/recovery, non-blocking research mutex, and daily
heartbeat. No strategy logic, risk parameters, or trading rules were modified.
All changes are infrastructure reliability improvements.

---

## Findings

| Item | Result | Notes |
|---|---|---|
| Strategy parameters unchanged | PASS | No trading logic touched |
| Offset persistence semantics | PASS | Offset advances on every acknowledged attempt — prevents non-idempotent command replay (`/market`, `/research`, `/deepdive`) |
| `cache_only` at hour==20 correctness | PASS | Verified: scheduler fires at 20:05 UTC, `datetime.now(UTC).hour` returns 20, `run_research(cache_only=True)` skips planning |
| Research mutex: two concurrent runs | PASS | Non-blocking acquisition; second caller skips immediately — `run_research()` called exactly once per concurrent pair |
| Heartbeat failure non-fatal | PASS | `except Exception: logger.warning()` — scheduler continues on heartbeat error |
| Backoff cap at 60s | PASS | `min(backoff * 2, 60)` — bounded, does not grow indefinitely |
| Offset file absent on first run | PASS | `_load_tg_offset()` returns 0 on `OSError` — safe bootstrap |
| No strategy calls in poll loop | PASS | `_telegram_poll_loop()` only dispatches commands — does not invoke research or planning directly |

---

## Critical Issues

None.

---

## Recommendations

1. The research mutex is non-reentrant `threading.Lock`. Document in developer notes that `run_research()` must never be called from within a context that holds `_RESEARCH_LOCK`.
2. Heartbeat at 00:01 UTC: if the process restarts at 00:00-00:01 UTC, the heartbeat may fire immediately. This is benign.

---

## Release Recommendation

```
APPROVED — no quant concerns
```

---
*Quant review: 2026-09-22*
