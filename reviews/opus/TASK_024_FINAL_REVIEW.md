# TASK-024 CTO Final Review

**Reviewer:** Lead CTO
**Task:** TASK-024 — Telegram Reliability and Research Scheduling
**Date:** 2026-09-22
**Tests:** 123 / 123 PASS

---

## Summary

TASK-024 is implemented correctly, completely, and within scope. All 8 required
tests are present, well-constructed, and non-trivial. No forbidden files were
touched. `handle_market()` still carries the global risk mutation (TASK-025
scope) — correctly left unchanged.

---

## Findings

### R1/R2 — Offset Persistence (Acknowledged Semantics)
**PASS**

`_load_tg_offset()` at `orchestrator.py:181-187`:
- Returns `int` from `tg_offset.json` via `_bot_data_path()`
- Handles all 5 failure modes cleanly → returns 0 (safe restart from zero)

`_save_tg_offset()` at `orchestrator.py:190-200`:
- Atomic `.tmp → os.replace()` pattern ✅
- `os.makedirs` ensures directory exists ✅
- Failure logs at `WARNING` (non-fatal, correct) ✅

Offset advance in poll loop at `orchestrator.py:589-591`:
```python
if isinstance(update_id, int):
    offset = max(offset, update_id + 1)
    _save_tg_offset(offset)
```
This sits **after** the per-update `try/except` block — offset advances on
every acknowledged attempt, including failed dispatches. Matches amended
semantics: prevents non-idempotent command replay on restart. ✅

`max(offset, update_id + 1)` correctly handles out-of-order update IDs. ✅

### R3 — Per-Update Exception Isolation
**PASS**

Each `_dispatch_telegram_update(update)` call is wrapped individually at
`orchestrator.py:585-588`. A dispatch failure logs the update_id and continues
to the next update. The offset still advances for the failed update (L589-591).
✅

### R4 — Outer Poll Loop Recovery with Backoff
**PASS**

Outer `try/except Exception` at `orchestrator.py:592-596`:
- `backoff` starts at 1, doubles per failure, caps at 60s ✅
- `recovering = True` flag set on failure ✅
- On next successful `get_updates()`, logs `telegram_poll_resumed` at WARNING
  and resets `backoff = 1` (L576-579) ✅
- `recovering` flag correctly suppresses resume log on clean runs ✅

### R4 (Amendment A) — Research Mutex: Both Paths Non-Blocking
**PASS**

`cmd_research()` at `orchestrator.py:134-150`:
- `_RESEARCH_LOCK.acquire(blocking=False)` ✅
- If not acquired: `research_already_running — scheduler_skip` log, returns ✅
- `finally: _RESEARCH_LOCK.release()` — lock always released even on exception ✅

`handle_research()` at `orchestrator.py:482-501`:
- `_RESEARCH_LOCK.acquire(blocking=False)` ✅
- If not acquired: sends "Research already in progress" Telegram message ✅
- Daemon thread spawned for non-blocking execution ✅
- `finally: _RESEARCH_LOCK.release()` inside the thread function ✅
- Sends "🔍 Research triggered — card incoming in ~30s" confirmation ✅

Lock release pattern: both paths use `acquire` + `finally: release` (not
`with` context manager). Correct — daemon thread cannot use `with` across
thread boundaries.

### R5 — Daily Heartbeat
**PASS**

`_heartbeat()` at `orchestrator.py:690-713`:
- Loads balance, active orders, cache mtime ✅
- Counts PENDING+FILLED only (`isinstance(order, dict)` guard for robustness) ✅
- `OSError` on missing cache file handled silently (shows "no cache") ✅
- Outer `except Exception: logger.warning(...)` — non-fatal ✅
- Telegram message contains: "alive", cache age, active count, balance ✅

Scheduler at `orchestrator.py:751-755`:
- `hour=0, minute=1` — fires 00:01 UTC daily ✅
- `max_instances=1` ✅

---

## Test Quality Assessment

**`test_offset_loaded_on_startup`** — writes `{"offset": 50}`, verifies
first `get_updates` call receives `offset=50`. Clean signal-style test. ✅

**`test_offset_saved_after_each_update`** — monkeypatches `_save_tg_offset`
and captures calls. Verifies three sequential updates produce offsets `[11, 12, 13]`.
Correctly confirms per-update (not per-batch) persistence. ✅

**`test_malformed_update_does_not_stop_polling`** — first update raises,
second update still dispatched. Confirms update_id=2 reaches the handler.
Correct isolation verification. ✅

**`test_poll_exception_triggers_backoff`** — `get_updates` throws `OSError`,
verifies `time.sleep(1)` called. Clean backoff trigger test. ✅

**`test_poll_recovers_after_exception`** — two `get_updates` calls, first
raises, second returns `[]`. Verifies `calls == 2`, `sleeps == [1, 1]` (backoff
sleep + idle sleep after empty batch), and `telegram_poll_resumed` in logs. ✅

**`test_concurrent_research_runs_once`** — uses `threading.Event` to hold
Thread 1 inside `run_research` while Thread 2 attempts. Thread 2 joins before
releasing Thread 1 — guarantees the non-blocking test fires during active lock.
Correct synchronization pattern. Real threads, not mocked. ✅

**`test_research_command_blocked_when_research_running`** — manually acquires
lock, calls `handle_research()`, verifies correct "in progress" message. ✅

**`test_heartbeat_sends_expected_fields`** — verifies "alive", balance, and
correct active-order count (PENDING+FILLED=2, CLOSED excluded). ✅

---

## Non-Findings (Verified Clean)

- `bybit_bot/config.py` — unchanged ✅
- `bybit_bot/monitor.py` — unchanged ✅
- `bybit_bot/execution.py` — unchanged ✅
- `docs/STRATEGY_SPEC.md`, `docs/RISK_SPEC.md`, `AGENTS.md` — unchanged ✅
- `handle_market()` global mutation retained (TASK-025 scope) ✅

---

## Release Recommendation

```
APPROVED
```

All 8 required tests pass. Implementation is correct, within scope, and
addresses both contract amendments (non-blocking mutex, acknowledged-offset
semantics) precisely.

**TASK-024 is closed.**

---

## Next Step

Assign **TASK-025** to Codex.

---
*CTO sign-off: 2026-09-22*
