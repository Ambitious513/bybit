# TASK-026 — Pre-GATE-3 Hardening
*CTO-amended version — 2026-09-23. Original proposal by team. Four amendments applied.*

## 1. Objective

Fix all P1 and P2 gaps identified in the CTO post-GATE-2 review (2026-09-23)
before live trading authorization. Covers: dead cat Phase 1 drop-volume filter,
`/filled` price validation, TRADFI perps audit, Telegram fallback channel,
HTTP connection pool warnings, and funding creep alerting.

> ⚠️ **SEQUENCING CONSTRAINT:** TASK-026 implementation must not begin until
> **TASK-027 (SL Algorithm Fix) is APPROVED by CTO.** Both tasks modify
> `sr_calculator.py`. Starting before TASK-027 is approved will cause a
> merge conflict. Read the TASK-027 completion report before starting R1.

## 2. Background

During paper trading sessions (2026-09-21 to 2026-09-23), six issues were
identified through live observation and adversarial review:

1. **Dead cat filter missing Phase 1** — `dead_cat_check()` in `sr_calculator.py`
   only checks the bounce candle (Phase 2). It does NOT check whether the drop
   candle entering the zone was high-volume (real selling) or low-volume (weak
   pullback). On 2026-09-22, BEATUSDT dropped into the zone on a 2.86× average
   volume candle — a genuine high-volume dump. Without Phase 1, the bot would
   have issued an entry card into a genuine breakdown.

2. **`/filled` accepts price ≤ 0** — No validation on the PRICE argument. A
   typo (e.g. `/filled LABUSDT 0 10:30`) writes `fill_price=0.0` to
   `active_orders.json`, causing all P&L calculations to return nonsense.

3. **TRADFI perps list unaudited** — `TRADFI_PERPS` in `config.py` lists
   COINUSDT, MSTRUSDT, XAUUSDT, NVDAUSDT. Not verified as currently active
   on Bybit linear perpetuals.

4. **No Telegram fallback for live trading** — During Telegram API outage,
   `/filled`, `/cancel`, and `/closed` are unreachable. For paper trading:
   acceptable. For live trading: open losing position could remain unmanaged.

5. **HTTP connection pool warnings** — `quickscan.py` fires concurrent Bybit
   API calls that saturate urllib3's default pool (size 10). Cosmetic but masks
   real errors in logs.

6. **Funding creep not monitored post-card** — Once a card is issued, the bot
   does not monitor whether funding crosses the crowded threshold (0.010%).
   LABUSDT funding rose from 0.005% to 0.012% between research cycles with no
   alert.

## 3. Source-of-Truth Documents

- `bybit_bot/config.py` — TRADFI_PERPS, CROWDED_FUNDING_THRESHOLD (read-only)
- `docs/STRATEGY_SPEC.md` (do NOT modify)
- `docs/RISK_SPEC.md` (do NOT modify)
- `AGENTS.md` v2.0

## 4. Scope

Pre-GATE-3 hardening only. No strategy parameters, scoring weights, or
risk parameters may be modified.

## 5. Allowed Files / Directories

- `bybit_bot/sr_calculator.py` — Phase 1 dead cat addition only
- `bybit_bot/orchestrator.py` — `/filled` validation + email fallback stub + TRADFI audit
- `bybit_bot/monitor.py` — funding creep alert only
- `bybit_bot/bybit_api.py` — connection pool cap only
- `bybit_bot/telegram.py` — email fallback function only
- `bybit_bot/quickscan.py` — max_workers cap only
- `bybit_bot/config.py` — ADD new constants only (no existing value changes)
- `bybit_bot/tests/test_pre_gate3.py` — NEW file

## 6. Forbidden Files / Directories

- `docs/STRATEGY_SPEC.md`
- `docs/RISK_SPEC.md`
- `AGENTS.md`
- `bybit_bot/planning.py`
- `bybit_bot/screener.py`
- `bybit_bot/research.py`
- `bybit_bot/execution.py`
- All existing test files (no modifications — additions only in new test file)

## 7. Requirements

### R1 — Dead Cat Filter Phase 1 (Drop Volume Check)

> ⚠️ **Read TASK-027 completion report first.** `sr_calculator.py` was
> modified by TASK-027. Base your changes on the post-TASK-027 file state.

In `bybit_bot/sr_calculator.py`, extend `dead_cat_check()` to add Phase 1
before the existing Phase 2 checks.

**CTO Amendment — guard must be `< 6` not `< 5`:**
```python
# CORRECT guard — needs 5 baseline candles + 1 bounce candle = 6 minimum
if not candles or len(candles) < 6:
    return _empty_dead_cat_result()
```
With exactly 5 candles, `candles[-6:-1]` silently returns only 4 baseline
candles. The guard `< 5` would allow this edge case to proceed with incorrect
baseline volume.

**Full Phase 1 implementation:**

```python
baseline_candles = candles[-6:-1]   # 5 candles before current
bounce_candle    = candles[-1]
prior_candle     = candles[-2]

baseline_vol = mean([c["volume"] for c in baseline_candles]) or 1.0

drop_candles = [
    c for c in baseline_candles
    if float(c["close"]) < float(c["open"])   # red candle
]
drop_vol = mean([c["volume"] for c in drop_candles]) if drop_candles else 0.0

drop_vol_ratio  = drop_vol / baseline_vol
drop_was_weak   = drop_vol_ratio < DROP_VOL_WEAK_THRESHOLD    # < 0.70
drop_was_strong = drop_vol_ratio > DROP_VOL_STRONG_THRESHOLD  # > 1.30

# Phase 1 passes when: weak drop (pullback), OR no red candles (price moved
# up into zone — no dump at all), OR neutral ratio (0.70–1.30 = only Phase 2 gates)
phase1_pass = not drop_was_strong
```

**Edge cases:**
- No red candles in `baseline_candles` → `drop_vol=0.0`, `drop_was_weak=True` → Phase 1 passes (no dump)
- `baseline_vol == 0` (new listing) → return `_empty_dead_cat_result()` with `passed=False`
- Neutral ratio (0.70–1.30): `drop_was_weak=False`, `drop_was_strong=False` → `phase1_pass=True` (only Phase 2 gates)

**Combined result:**
```python
passed = phase1_pass and close_above_mid and vol_ok and higher_low

return {
    "passed":               passed,
    "drop_vol_ratio":       round(drop_vol_ratio, 2),
    "drop_was_weak":        drop_was_weak,
    "drop_was_strong":      drop_was_strong,
    "bounce_above_midpoint": close_above_mid,
    "bounce_volume_ok":     vol_ok,
    "higher_low":           higher_low,
    "warning": (
        f"HIGH VOLUME DROP ({drop_vol_ratio:.1f}x avg) — "
        "wait for zone retest confirmation"
        if drop_was_strong else None
    ),
}
```

Add to `config.py`:
```python
DROP_VOL_WEAK_THRESHOLD   = 0.70
DROP_VOL_STRONG_THRESHOLD = 1.30
BOUNCE_VOL_MIN_RATIO      = 0.70
```

---

### R2 — `/filled` Price Validation

In `orchestrator.py`, `handle_filled()`, add validation immediately after
parsing the PRICE argument and before any write to `active_orders.json`:

```python
if fill_price <= 0:
    telegram.send_message(
        f"❌ Invalid fill price: {parts[2]}\n"
        f"Price must be a positive number.\n"
        f"Example: /filled LABUSDT 0.06045 10:32"
    )
    return
```

---

### R3 — TRADFI Perps Audit on Startup

Add to `orchestrator.py`, called once in the startup sequence after API
connectivity is confirmed and before the scheduler starts:

```python
def audit_tradfi_perps() -> None:
    """Verify all TRADFI_PERPS symbols are active on Bybit linear perps."""
    from bybit_bot.config import TRADFI_PERPS
    inactive = []
    for symbol in TRADFI_PERPS:
        result = bybit_api.get_ticker(symbol)
        if result is None or _float(result.get("price")) <= 0:
            inactive.append(symbol)
    if inactive:
        telegram.send_message(
            f"⚠️ TRADFI PERPS AUDIT — {len(inactive)} INACTIVE:\n"
            + "\n".join(f"  ❌ {s}" for s in inactive)
            + "\n→ Remove from TRADFI_PERPS in config.py"
        )
        logger.warning("tradfi_audit_inactive symbols=%s", inactive)
    else:
        logger.info("tradfi_audit_ok all_%d_symbols_active", len(TRADFI_PERPS))
```

A failed audit sends a Telegram warning but does NOT prevent the bot from
starting.

---

### R4 — Telegram Fallback (Email Stub)

**CTO Amendment — ALL credentials must load from `.env` via `os.getenv()`.**
No literal string defaults for credentials in `config.py`.

Add to `config.py` (new constants only):
```python
import os  # already imported

EMAIL_FALLBACK_ENABLED = False
EMAIL_FROM    = os.getenv("EMAIL_FROM", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
```

Add to `bybit_bot/telegram.py`:

```python
def send_message_with_fallback(text: str) -> bool:
    """Send via Telegram; fall back to email if Telegram fails."""
    success = send_message(text)
    if not success and EMAIL_FALLBACK_ENABLED:
        return _send_email_fallback(
            subject="[Bybit Bot] TELEGRAM FAILED — URGENT",
            body=text,
        )
    return success


def _send_email_fallback(subject: str, body: str) -> bool:
    import smtplib, ssl
    from email.message import EmailMessage
    try:
        msg = EmailMessage()
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(context=ctx)
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        logger.info("email_fallback_sent subject=%r", subject)
        return True
    except Exception as exc:
        logger.error("email_fallback_failed exc=%s", exc)
        return False
```

Use `send_message_with_fallback()` for CRITICAL alerts in `monitor.py`
(SL hit, TP1 hit, time-stop, hard close, BTC invalidation).
Use plain `send_message()` for informational messages.

Add `/test_email` command stub to `orchestrator.py` (operator must confirm
receipt before GATE-3).

**GATE-3 hard prerequisite:** `EMAIL_FALLBACK_ENABLED=True` must be set in
`.env`, operator must run `/test_email`, and confirm receipt before human
GATE-3 approval.

---

### R5 — HTTP Connection Pool Cap

In `bybit_bot/bybit_api.py`, configure the requests Session with explicit
pool size:

```python
from requests.adapters import HTTPAdapter

_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://",  _ADAPTER)
```

In `quickscan.py`, cap the ThreadPoolExecutor:
```python
from bybit_bot.config import QUICKSCAN_MAX_WORKERS

with ThreadPoolExecutor(max_workers=QUICKSCAN_MAX_WORKERS) as executor:
    ...
```

Add to `config.py`:
```python
QUICKSCAN_MAX_WORKERS = 8
```

---

### R6 — Funding Creep Alert in Monitor

In `monitor.py`, add a funding creep check for each FILLED order. Alert fires
AT MOST ONCE per order (tracked via `funding_warned` flag in the order dict):

```python
def check_funding_creep(order: dict, current_funding: float) -> str | None:
    """Return warning if funding crossed crowded threshold post-fill."""
    if order.get("funding_warned"):
        return None
    if abs(current_funding) >= CROWDED_FUNDING_THRESHOLD:
        return (
            f"⚠️ FUNDING CREEP — {order['symbol']}\n"
            f"   Funding now: {current_funding*100:.4f}%\n"
            f"   Threshold:   {CROWDED_FUNDING_THRESHOLD*100:.4f}%\n"
            f"→ Position is now in overcrowded territory\n"
            f"→ Consider closing RUNNER early\n"
            f"→ Keep CORE — do NOT override TP1"
        )
    return None
```

When alert fires, set `order["funding_warned"] = True` and save to
`active_orders.json` before sending the Telegram message.

Add to `config.py` if not already present:
```python
CROWDED_FUNDING_THRESHOLD = 0.0005   # 0.05% = crowded funding
```

## 8. Acceptance Criteria

- R1–R6 all implemented
- Dead cat Phase 1 guard uses `< 6` (CTO amendment)
- `/filled` rejects price ≤ 0 with clear user message
- TRADFI audit fires on startup; Telegram warning for inactive symbols
- Email credentials load from `.env` via `os.getenv()` (CTO amendment)
- Email fallback stub present; `EMAIL_FALLBACK_ENABLED=False` by default
- Connection pool warnings eliminated from quickscan logs
- Funding creep alert fires once per order when funding crosses threshold
- Full test suite passes — zero regressions

## 9. Interfaces / Contracts

### New `config.py` Constants (additive only)

```python
DROP_VOL_WEAK_THRESHOLD   = 0.70
DROP_VOL_STRONG_THRESHOLD = 1.30
BOUNCE_VOL_MIN_RATIO      = 0.70
QUICKSCAN_MAX_WORKERS     = 8
EMAIL_FALLBACK_ENABLED    = False
EMAIL_FROM    = os.getenv("EMAIL_FROM", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
CROWDED_FUNDING_THRESHOLD = 0.0005
```

## 10. Required Tests

Create `bybit_bot/tests/test_pre_gate3.py`:

**Dead Cat Phase 1:**
1. `test_dead_cat_phase1_high_vol_drop_blocks_entry` — drop_vol_ratio=2.5 → passed=False
2. `test_dead_cat_phase1_low_vol_drop_allows_phase2` — drop_vol_ratio=0.5, clean Phase 2 → passed=True
3. `test_dead_cat_phase1_no_red_candles_treated_as_weak` — no red candles → drop_was_weak=True
4. `test_dead_cat_phase1_neutral_vol_clean_phase2_passes` — ratio=1.0 + clean Phase 2 → passed=True
5. `test_dead_cat_phase1_neutral_vol_failed_phase2_blocks` — ratio=1.0 + failed Phase 2 → passed=False *(CTO amendment — both sub-cases required)*
6. `test_dead_cat_warning_message_present_on_strong_drop` — warning not None when ratio>1.30
7. `test_dead_cat_all_phases_pass_on_clean_setup` — low drop vol + clean bounce → passed=True

**`/filled` Validation:**
8. `test_filled_rejects_zero_price` — price=0 → no write to active_orders.json
9. `test_filled_rejects_negative_price` — price=-1.5 → no write
10. `test_filled_accepts_valid_price` — price=0.06045 → order updated correctly

**TRADFI Audit:**
11. `test_tradfi_audit_sends_warning_for_inactive_symbol` — mock inactive → Telegram warning sent
12. `test_tradfi_audit_silent_when_all_active` — all active → no Telegram message

**Funding Creep:**
13. `test_funding_creep_alert_fires_once` — second monitor cycle does not re-alert
14. `test_funding_creep_no_alert_below_threshold` — 0.0003 → no alert

**Connection Pool:**
15. `test_quickscan_executor_respects_max_workers` — ThreadPoolExecutor capped at QUICKSCAN_MAX_WORKERS

## 11. Expected Deliverables

- Modified: `bybit_bot/sr_calculator.py` (Dead Cat Phase 1 only)
- Modified: `bybit_bot/orchestrator.py`
- Modified: `bybit_bot/monitor.py`
- Modified: `bybit_bot/bybit_api.py`
- Modified: `bybit_bot/telegram.py`
- Modified: `bybit_bot/quickscan.py`
- Modified: `bybit_bot/config.py` (additive constants only)
- New: `bybit_bot/tests/test_pre_gate3.py`
- Completion report (Article 9 format)
- Email fallback test confirmation note (operator sign-off required at GATE-3)

## 12. Failure / Escalation Conditions

STOP and escalate to CTO if:

- Dead cat Phase 1 refactor requires changes to `STRATEGY_SPEC.md`
- Email fallback requires credentials in source files (must use `.env` only)
- TRADFI audit reveals all 4 symbols inactive (systemic API issue — do not auto-remove)
- Funding creep alert requires changing `CROWDED_FUNDING_THRESHOLD` value

## 13. Completion Report Requirements

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list with line ranges]
Dead Cat Phase 1: [IMPLEMENTED / BLOCKED — reason]
Filled Validation: [IMPLEMENTED / BLOCKED — reason]
TRADFI Audit Result: [all active / N inactive — symbols]
Email Fallback: [STUB PRESENT / operator tested: YES/NO]
Connection Pool: [warnings eliminated YES/NO]
Funding Creep: [IMPLEMENTED / BLOCKED — reason]
Tests Run: [pytest output summary]
Tests Passed: [count]
Tests Failed: [count + names + errors]
Known Issues: [unresolved]
GATE-3 Blockers: [any remaining items before live]
Recommended Next Step: CTO review → operator email test → GATE-3 decision
```

## 14. Review Plan

```
Codex implements → Sonnet quant review → Gemini adversarial → CTO final review
```

## 15. Skill Extraction Decision

No skill to be extracted.

## 16. Status / Sign-off

```
Status:              READY FOR IMPLEMENTATION
                     Begin ONLY after TASK-027 CTO APPROVED
Assigned to:         Codex
CTO sign-off:        2026-09-23
Gate dependency:     Must complete + full review pipeline before GATE-3 decision
GATE-3 prerequisite: Email fallback operator-tested and confirmed
```
