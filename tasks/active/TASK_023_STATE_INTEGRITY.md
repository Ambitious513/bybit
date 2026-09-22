# TASK-023 — State Integrity and Execution Safety

## 1. Objective

Fix all P0 state-integrity issues and the two critical execution-safety gaps that
undermine paper-trading result accuracy. No strategy parameters are modified.

## 2. Background

The CTO + Codex joint review (2026-09-22) identified:
- No process-wide lock on `active_orders.json` — monitor, execution, and Telegram
  command handlers independently read-modify-write the file; a concurrent update can
  silently overwrite a valid state change.
- No session cap or duplicate-card check in `execution.generate_card()`.
- Research fires at an interval offset from process start, not at 4H candle boundaries.
- No minimum stop-distance guard (data anomaly could produce $20,000+ notional on
  a $2 risk if stop_dist_pct ≈ 0.01%).
- No hard-close time guard in `generate_card()` — cards can be issued after 20:00 UTC.
- The 20:05 UTC research run invokes `run_planning()` which sends actionable setup
  messages after trading is closed for the day.
- `/filled` uses `price` in the confirmation message but does not store it; P&L
  uses the planned entry price, not the user's actual fill price.

## 3. Source-of-Truth Documents

- `docs/STRATEGY_SPEC.md` (GATE-1 locked — do NOT modify)
- `docs/RISK_SPEC.md` (GATE-1 locked — do NOT modify)
- `AGENTS.md` v2.0
- `bybit_bot/config.py` — all GATE-1 constants (read-only reference)

## 4. Scope

Fix the listed bugs. No other changes.

## 5. Allowed Files / Directories

- `bybit_bot/execution.py`
- `bybit_bot/monitor.py`
- `bybit_bot/orchestrator.py`
- `bybit_bot/research.py` — add `cache_only: bool = False` parameter only
- `bybit_bot/tests/test_state_integrity.py` — NEW file

## 6. Forbidden Files / Directories

- `docs/STRATEGY_SPEC.md`
- `docs/RISK_SPEC.md`
- `AGENTS.md`
- `bybit_bot/config.py`
- `bybit_bot/screener.py`
- `bybit_bot/planning.py`
- `bybit_bot/sr_calculator.py`
- `bybit_bot/telegram.py`
- `bybit_bot/bybit_api.py`
- `bybit_bot/quickscan.py`
- All existing test files

## 7. Requirements

### R1 — Shared Process-Wide Lock

Create `_ORDERS_LOCK = threading.Lock()` at module level in `monitor.py`.
Export it so other modules can import it:

```python
# bybit_bot/monitor.py
import threading
_ORDERS_LOCK = threading.Lock()
```

Import and use in `orchestrator.py` and `execution.py`:

```python
from bybit_bot.monitor import _ORDERS_LOCK
```

Every `active_orders.json` read-modify-write sequence in all three files
must acquire `_ORDERS_LOCK` before reading and hold it until after
the atomic write (`os.replace`) completes.

Affected operations:
- `execution.save_pending_order()` — wrap load + append + atomic write
- `monitor.run_monitor_cycle()` — wrap load + state change + atomic write
- `orchestrator.handle_filled()` — wrap load + update + atomic write
- `orchestrator.handle_closed()` — wrap load + update + atomic write
- `orchestrator.handle_cancel()` — wrap load + filter + atomic write
- `orchestrator._save_active_orders()` — the save itself must be inside
  the caller's lock context, not a separate lock acquisition

### R2 — Session Cap (Atomic, Inside Lock)

In `execution.generate_card()`, after acquiring the lock and loading orders:

```python
active_count = sum(
    1 for o in existing
    if o.get("status") in {"PENDING", "FILLED"}
)
if active_count >= MAX_TRADES_PER_SESSION:
    logger.info("session_cap_reached symbol=%s active=%s",
                setup.get("symbol"), active_count)
    return None
```

`MAX_TRADES_PER_SESSION` is imported from `bybit_bot.config` (value: 3).
No Telegram message for this — the planning summary already informed the user.

### R3 — Symbol Deduplication (Atomic, Inside Lock)

In the same lock block in `execution.generate_card()`:

```python
existing_symbols = {
    o.get("symbol") for o in existing
    if o.get("status") in {"PENDING", "FILLED"}
}
if str(setup.get("symbol", "")) in existing_symbols:
    logger.info("duplicate_card_skipped symbol=%s", setup.get("symbol"))
    return None
```

Block on both PENDING and FILLED to prevent issuing a second card for an
already-active position.

### R4 — Hard-Close Time Guard

In `execution.generate_card()`, before lock acquisition (read-only check):

```python
from bybit_bot.config import HARD_CLOSE_UTC_HOUR
now = datetime.now(UTC)
if now.hour >= HARD_CLOSE_UTC_HOUR:
    logger.info("card_blocked_hard_close symbol=%s hour=%s",
                setup.get("symbol"), now.hour)
    return None
```

`HARD_CLOSE_UTC_HOUR = 20` (from config). Cards must not be issued at 20:00
or later UTC.

### R5 — Research Cron Alignment + Cache-Only 20:05 Run

**Scheduler change** in `orchestrator.cmd_daemon()`:

```python
scheduler.add_job(
    cmd_research, "cron",
    hour="0,4,8,12,16,20", minute=5,
    id="research", name="Full research pipeline",
    max_instances=1,
)
```

**`cmd_research()` change:**

```python
def cmd_research() -> None:
    now = datetime.now(UTC)
    cache_only = (now.hour == 20)
    try:
        from bybit_bot import research
        research.run_research(cache_only=cache_only)
    except ...
```

**`research.run_research()` change** — add parameter:

```python
def run_research(cache_only: bool = False) -> None:
    ...
    # At the point where run_planning() would be called:
    if cache_only:
        logger.info("research_cache_only_mode — skipping planning and execution cards")
        return
    run_planning()
```

The 20:05 run classifies BTC regime and writes the cache for the monitor's
regime-flip detection. It does NOT call `run_planning()` and sends NO planning
or execution messages.

### R6 — `/filled` Dual-Field Semantics

In `orchestrator.handle_filled()`, update the matched order:

```python
order["status"] = "FILLED"
order["fill_time_utc"] = fill_time
order["planned_entry"] = order.get("entry")   # preserve original planned entry
order["fill_price"] = price                   # store user's actual fill price
```

Update `monitor.calculate_pnl()` to use `fill_price` when present:

```python
effective_entry = _float(order.get("fill_price") or order.get("entry"))
```

Update `monitor.log_trade()` to include both:

```python
"planned_entry": _float(order.get("planned_entry", order.get("entry"))),
"fill_price": _float(order.get("fill_price", order.get("entry"))),
```

Backwards compatibility: existing FILLED orders without `fill_price` fall
back to `entry` silently.

### R7 — Minimum Stop-Distance Guard (Engineering Safety — CTO Approved)

In `execution.generate_card()`, immediately after the `>= 8.0` guard:

```python
if stop_dist_pct < 0.3:
    logger.warning(
        "stop_too_tight symbol=%s stop_dist_pct=%.4f",
        setup.get("symbol"), stop_dist_pct,
    )
    return None
```

Rationale: prevents position-sizing blowup from data anomalies. This is the
same category as the existing 8% maximum guard, which was implemented in
TASK-019 as an engineering safety guardrail without a strategy-change proposal.
CTO has approved treating both consistently.

### R8 — Existing 105 Tests Must Pass

Run the full test suite. Zero regressions permitted.

## 9. Interfaces / Contracts

### Lock Export Contract

```python
# bybit_bot/monitor.py
_ORDERS_LOCK: threading.Lock  # exported, imported by execution.py and orchestrator.py
```

### `run_research()` Signature Change

```python
def run_research(cache_only: bool = False) -> None:
```

Default `False` preserves existing behaviour for `--research` CLI and all tests.

### Order Schema Additions (new optional fields)

```json
{
  "planned_entry": float,   // set at /filled time
  "fill_price":    float    // set at /filled time
}
```

Existing orders without these fields continue to work via fallback to `"entry"`.

## 10. Acceptance Criteria

- All R1–R8 requirements implemented
- Full test suite passes (105 + new tests)
- No strategy parameters modified
- `config.py` unchanged
- `STRATEGY_SPEC.md` and `RISK_SPEC.md` unchanged
- VPS deployment: `git push` only (no manual files)

## 11. Required Tests

Create `bybit_bot/tests/test_state_integrity.py` with at minimum:

1. `test_session_cap_blocks_fourth_card` — 3 active orders → `generate_card` returns None
2. `test_deduplication_blocks_pending_symbol` — PENDING XUSDT → second card returns None
3. `test_deduplication_blocks_filled_symbol` — FILLED XUSDT → new card returns None
4. `test_hard_close_guard_blocks_card` — mock `datetime.now` to 20:30 UTC → returns None
5. `test_20_00_research_is_cache_only` — mock hour=20, verify `run_planning` NOT called
6. `test_filled_preserves_planned_entry` — planned entry unchanged after `/filled`
7. `test_filled_stores_fill_price` — `fill_price` field set to user-supplied price
8. `test_pnl_uses_fill_price_over_planned_entry` — P&L calc uses fill_price when present
9. `test_minimum_stop_guard_rejects_tight_stop` — stop_dist_pct=0.1 → returns None
10. `test_concurrent_writes_no_lost_update` — two threads simultaneously call
    `save_pending_order`; both orders present in final state (threading simulation,
    NOT mocked — use real `threading.Thread`)

## 12. Expected Deliverables

- Modified: `bybit_bot/execution.py`
- Modified: `bybit_bot/monitor.py`
- Modified: `bybit_bot/orchestrator.py`
- Modified: `bybit_bot/research.py` (cache_only param only)
- New: `bybit_bot/tests/test_state_integrity.py`
- Completion report (Article 9 format)

## 13. Failure / Escalation Conditions

STOP and escalate to CTO if:
- Any test in the existing suite fails and the fix requires changing strategy logic
- The lock design requires modifying `planning.py` or `screener.py`
- `run_research()` signature change breaks imports in unexpected ways
- The concurrency test reveals a design flaw not addressable within this scope

## 14. Completion Report Requirements

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list every modified file with line ranges]
Tests Run: [pytest command and output summary]
Tests Passed: [count]
Tests Failed: [count + names + errors]
Known Issues: [any unresolved problems]
Remaining Risks: [identified but unaddressed risks]
Recommended Next Step: TASK-024
```

## 15. Review Plan

```
Codex implements → Sonnet quant review → Gemini adversarial review → CTO final review
```

## 16. Skill Extraction Decision

No skill to be extracted from this task (bug fixes, not new capability).

## 17. Status / Sign-off

```
Status: READY FOR IMPLEMENTATION
Assigned to: Codex
CTO sign-off: 2026-09-22
Gate dependency: Must complete before GATE-3 decision
```
