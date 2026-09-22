# TASK-024 — Telegram Reliability and Research Scheduling

## 1. Objective

Harden the Telegram polling daemon against crashes and command replay,
prevent `/research` from overlapping scheduled research, and add a daily
heartbeat for operational visibility.

## 2. Background

The CTO + Codex joint review (2026-09-22) identified:
- Telegram `offset` resets to 0 on every restart — old commands (including
  non-idempotent `/market`, `/research`, `/deepdive`) replay after each
  service restart or crash.
- The poll loop has no outer exception handler — a network error or SSL
  failure kills the daemon thread silently; the scheduler keeps running but
  all Telegram commands stop working with no alert.
- `handle_research()` spawns a background thread with no guard; if the
  APScheduler fires `cmd_research()` concurrently, two research pipelines
  run simultaneously and overwrite each other's cache.
- There is no operational health signal — if the scheduler silently stalls,
  the operator has no way to detect it until a trade opportunity is missed.

## 3. Source-of-Truth Documents

- `AGENTS.md` v2.0
- `bybit_bot/config.py` (read-only reference)
- `docs/STRATEGY_SPEC.md` (do NOT modify)

## 4. Scope

Reliability and scheduling improvements only. No strategy logic touched.

## 5. Allowed Files / Directories

- `bybit_bot/orchestrator.py`
- `bybit_bot/tests/test_reliability.py` — NEW file

## 6. Forbidden Files / Directories

- All files not listed in section 5
- `bybit_bot/config.py`
- `docs/STRATEGY_SPEC.md`
- `docs/RISK_SPEC.md`
- `AGENTS.md`
- All existing test files

## 7. Requirements

### R1 — Persist Telegram Offset to Disk

Save the offset to `bybit_bot/data/tg_offset.json` atomically after each
successfully handled update (not per-batch — per-update):

```python
def _load_tg_offset() -> int:
    try:
        path = _bot_data_path("tg_offset.json")
        with open(path, "r", encoding="utf-8") as f:
            return int(json.load(f).get("offset", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0

def _save_tg_offset(offset: int) -> None:
    path = _bot_data_path("tg_offset.json")
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("tg_offset_save_failed error=%s", exc)
```

Initialize in `_telegram_poll_loop()`:

```python
offset = _load_tg_offset()
```

Save after each update is processed (inside the per-update loop):

```python
if isinstance(update_id, int):
    offset = max(offset, update_id + 1)
    _save_tg_offset(offset)
```

### R2 — Per-Update Exception Isolation

Wrap each `_dispatch_telegram_update(update)` call individually:

```python
for update in updates:
    update_id = update.get("update_id") if isinstance(update, dict) else None
    try:
        _dispatch_telegram_update(update)
    except Exception as exc:
        logger.error("dispatch_failed update_id=%s error=%s", update_id, exc)
    if isinstance(update_id, int):
        offset = max(offset, update_id + 1)
        _save_tg_offset(offset)
```

A handler crash must not stop processing subsequent updates in the same batch.

### R3 — Outer Poll Loop Recovery with Backoff

The outer `while True` loop must recover from any exception with exponential
backoff:

```python
def _telegram_poll_loop() -> None:
    offset = _load_tg_offset()
    backoff = 1
    while True:
        try:
            updates = telegram.get_updates(offset=offset, timeout=30)
            backoff = 1   # reset on success
            if not updates:
                time.sleep(1)
                continue
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                try:
                    _dispatch_telegram_update(update)
                except Exception as exc:
                    logger.error("dispatch_failed update_id=%s error=%s", update_id, exc)
                if isinstance(update_id, int):
                    offset = max(offset, update_id + 1)
                    _save_tg_offset(offset)
        except Exception as exc:
            logger.error(
                "telegram_poll_error error=%s — retrying in %ds", exc, backoff
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
```

Log a WARNING (not ERROR) when polling successfully resumes after a failure.

### R4 — Research Mutex (Prevent `/research` + Scheduler Overlap)

Add a module-level non-reentrant lock in `orchestrator.py`:

```python
_RESEARCH_LOCK = threading.Lock()
```

In `cmd_research()` (scheduler path — blocking acquire):

```python
def cmd_research() -> None:
    with _RESEARCH_LOCK:
        now = datetime.now(UTC)
        cache_only = (now.hour == 20)
        try:
            from bybit_bot import research
            research.run_research(cache_only=cache_only)
        except ...
```

In `handle_research()` (Telegram command — non-blocking):

```python
def handle_research() -> None:
    if not _RESEARCH_LOCK.acquire(blocking=False):
        telegram.send_message("🔍 Research already in progress — card incoming shortly")
        return
    try:
        from bybit_bot import research
        threading.Thread(
            target=_research_with_lock_release,
            name="research-command",
            daemon=True,
        ).start()
    except Exception as exc:
        _RESEARCH_LOCK.release()
        raise

def _research_with_lock_release() -> None:
    try:
        from bybit_bot import research
        research.run_research()
    finally:
        _RESEARCH_LOCK.release()
```

### R5 — Daily Heartbeat

Add a cron job at 00:01 UTC in `cmd_daemon()`:

```python
scheduler.add_job(
    _heartbeat, "cron", hour=0, minute=1,
    id="heartbeat", name="Daily heartbeat",
    max_instances=1,
)
```

Implement `_heartbeat()`:

```python
def _heartbeat() -> None:
    try:
        balance = _load_balance()
        orders = load_active_orders()
        active = sum(1 for o in orders if o.get("status") in {"PENDING", "FILLED"})
        cache_age = "no cache"
        try:
            cache_path = _bot_data_path("research_cache.json")
            mtime = os.path.getmtime(cache_path)
            age_mins = int((time.time() - mtime) / 60)
            cache_age = f"{age_mins}m ago"
        except OSError:
            pass
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        telegram.send_message(
            f"✅ <b>Bybit Sniper alive</b> | {now}\n"
            f"Research cache: {cache_age}\n"
            f"Active orders: {active} | Balance: ${_float(balance.get('balance')):.2f}"
        )
    except Exception as exc:
        logger.warning("heartbeat_failed error=%s", exc)
```

Heartbeat failure is non-fatal — log at WARNING only, never crash the scheduler.

### R6 — Existing Tests Must Pass

Run full suite. Zero regressions permitted.

## 9. Interfaces / Contracts

### New data file

`bybit_bot/data/tg_offset.json` — schema: `{"offset": int}`

Absent or corrupt file → silently starts from 0 (safe: Bybit Telegram
only delivers recent unprocessed updates on reconnect).

### `_RESEARCH_LOCK` export

Not exported. Internal to `orchestrator.py` only.

## 10. Acceptance Criteria

- All R1–R5 implemented
- Full test suite passes
- No config.py or strategy files modified
- `tg_offset.json` written to `data/` directory (same as other state files)

## 11. Required Tests

Create `bybit_bot/tests/test_reliability.py` with at minimum:

1. `test_offset_loaded_on_startup` — mock file with offset=50; poll loop
   starts at 50, not 0
2. `test_offset_saved_after_each_update` — process 3 updates; verify file
   written 3 times with incremented values
3. `test_malformed_update_does_not_stop_polling` — inject update that raises
   in dispatch; verify next update still processed
4. `test_poll_exception_triggers_backoff` — mock `get_updates` to raise;
   verify `time.sleep` called with backoff value
5. `test_poll_recovers_after_exception` — mock raises once then succeeds;
   verify polling resumes (backoff resets to 1)
6. `test_concurrent_research_runs_once` — two threads call `cmd_research`
   simultaneously; verify `run_research` called exactly once (threading
   simulation, NOT mocked)
7. `test_research_command_blocked_when_research_running` — lock held; verify
   Telegram message "already in progress" sent instead of starting research
8. `test_heartbeat_sends_expected_fields` — mock balance/orders/cache; verify
   message contains "alive", balance, and order count

## 12. Expected Deliverables

- Modified: `bybit_bot/orchestrator.py`
- New: `bybit_bot/tests/test_reliability.py`
- Completion report (Article 9 format)

## 13. Failure / Escalation Conditions

STOP and escalate if:
- Lock design requires modifying files outside allowed scope
- `tg_offset.json` design conflicts with existing data directory structure
- Heartbeat requires a new dependency not in `requirements.txt`

## 14. Completion Report Requirements

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list with line ranges]
Tests Run: [pytest output summary]
Tests Passed: [count]
Tests Failed: [count + names + errors]
Known Issues: [unresolved]
Remaining Risks: [identified but unaddressed]
Recommended Next Step: TASK-025
```

## 15. Review Plan

```
Codex implements → Sonnet review → Gemini adversarial → CTO final review
```

## 16. Skill Extraction Decision

No skill to be extracted (reliability hardening, not new capability).

## 17. Status / Sign-off

```
Status: READY FOR IMPLEMENTATION (begin after TASK-023 approved)
Assigned to: Codex
CTO sign-off: 2026-09-22
Gate dependency: Must complete before GATE-3 decision
```
