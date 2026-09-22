# TASK-024 Adversarial / Red-Team Review

**Reviewer:** Adversarial Reviewer (Gemini role)
**Task ID:** TASK-024 — Telegram Reliability and Research Scheduling
**Date:** 2026-09-22

> **Pipeline note:** This review was conducted as part of the CTO review cycle.
> See TASK-023 red-team review for pipeline context.

---

## Summary

TASK-024 hardens the Telegram polling daemon and research scheduling. Review
focused on failure cascade risk, lock release guarantees, and offset
persistence edge cases.

---

## Findings

| Item | Result | Notes |
|---|---|---|
| Lock release on daemon thread exception | PASS | `_run()` in `handle_research()` has `finally: _RESEARCH_LOCK.release()` — lock cannot be permanently held on crash |
| Lock release on scheduler exception | PASS | `cmd_research()` has `finally: _RESEARCH_LOCK.release()` |
| Daemon thread orphan if `start()` raises | WARN | If `threading.Thread.start()` raises (OOM), lock is released immediately. Thread never runs. Correct. |
| Poll loop: infinite `while True` with no exit | PASS | Intentional daemon loop — killed when main process exits |
| Offset save failure: polling continues | PASS | `_save_tg_offset()` logs WARNING and returns — poll loop not interrupted |
| Backoff: first sleep is always 1s | PASS | `backoff = 1` before loop; correct initial delay |
| `recovering` flag thread safety | PASS | Only written and read within `_telegram_poll_loop()` — single thread, no race |
| Telegram outage: commands unresponsive | WARN | During outage, `/filled` and `/cancel` from Telegram are unreachable. Documented in Codex completion report. No mitigation needed for paper trading — operator can wait for recovery. |
| Heartbeat fires once per day: timing drift | PASS | APScheduler cron handles DST and timezone correctly with `timezone="UTC"` |
| Research mutex: `/research` + scheduled research exact-same-second race | PASS | Both use non-blocking acquire — one wins, one skips. Confirmed by real-thread test. |
| `_RESEARCH_LOCK` never exported | PASS | Module-internal — no risk of external callers holding the lock |

---

## Critical Issues

None that block paper trading.

---

## Recommendations

1. (P3) Consider adding a `/health` Telegram command that reports scheduler job last-run times for operator visibility.
2. (P2) Before GATE-3: if Telegram outage occurs during live trading, operator needs an alternative to confirm fills. Consider email/SMS fallback notification.
3. (P3) The `time.sleep(1)` on empty batch (poll loop L581) adds 1s minimum latency to all commands. Acceptable at paper scale; review for live.

---

## Release Recommendation

```
APPROVED_WITH_FIXES — approved for paper trading; P2 fallback notification recommended before GATE-3
```

---
*Adversarial review: 2026-09-22*
