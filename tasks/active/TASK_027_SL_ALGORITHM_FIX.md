# TASK-027 — SL Algorithm Fix: Nearest Support Below Zone

## 1. Objective

Fix a critical bug in `sr_calculator.py` where `_find_sl()` selects the
global minimum support cluster across all timeframes instead of the nearest
confirmed support cluster strictly below the entry zone bottom. This causes
stop distances of 30–40% on every setup, making all deep dives fail the 8%
stop guard and preventing any execution cards from being issued.

## 2. Background

On 2026-09-23, CTO inspection of `sr_calculator.py` confirmed the bug at
**line 169**:

```python
# BROKEN — picks the cluster with the lowest absolute price (global minimum)
deepest = min(confirmed, key=lambda cluster: cluster.get("bottom", float("inf")))
return float(deepest["bottom"]) * (1 - 0.003)
```

`min()` with key `"bottom"` selects the cluster with the lowest absolute
price across all timeframes — including 1D Fibonacci bottoms and 1D Volume
Profile lows that are 30–40% below the current trading range.

Live data confirming the bug (2026-09-23):

| Symbol   | Zone Bottom | Bot SL    | Stop %  | Correct SL | Correct % |
|----------|-------------|-----------|---------|------------|-----------|
| LABUSDT  | $0.05930    | $0.03639  | 38.7%   | ~$0.05865  | 1.0%      |
| MNTUSDT  | $0.66533    | $0.42113  | 36.7%   | ~$0.66122  | 0.6%      |
| ONDOUSDT | $0.32150    | $0.29252  | 9.0%    | ~$0.31350  | 2.5%      |

All three stops anchor to the lowest 1D support level on the chart. The
correct algorithm is: find the NEAREST support cluster whose top is strictly
below entry_zone_bottom. Current code finds the cluster with the MINIMUM
bottom — these are not the same when 1D supports are far below the range.

This is the sole reason every deep dive outputs "stop too wide — skip".

## 3. Source-of-Truth Documents

- `docs/STRATEGY_SPEC.md` Section 6.1 (do NOT modify):
  > SL = deepest confirmed support cluster below entry_zone_bottom + 0.3% buffer
  
  "Deepest confirmed" = nearest (highest value) below — not global minimum.
  "Deepest" refers to structural depth (closest to zone), not lowest absolute price.

- `docs/RISK_SPEC.md` (do NOT modify)
- `AGENTS.md` v2.0

## 4. Scope

Single function fix in `sr_calculator.py`. No strategy parameters changed.
No risk parameters changed. Callers in `planning.py` and `execution.py`
do NOT need to be updated — they access SL via `get_sr_levels()` which
already returns a dict; the fix is internal to `_find_sl()`.

## 5. Allowed Files / Directories

- `bybit_bot/sr_calculator.py` — `_find_sl()` fix + R4 filter + enriched
  `get_sr_levels()` return dict (additive fields only)
- `bybit_bot/tests/test_sl_algorithm.py` — NEW file

## 6. Forbidden Files / Directories

- `docs/STRATEGY_SPEC.md`
- `docs/RISK_SPEC.md`
- `AGENTS.md`
- `bybit_bot/config.py`
- `bybit_bot/planning.py`
- `bybit_bot/execution.py`
- `bybit_bot/monitor.py`
- `bybit_bot/research.py`
- All existing test files (no modifications)

## 7. Requirements

### R1 — Fix `_find_sl()` Selection Logic

In `bybit_bot/sr_calculator.py`, change `_find_sl()` at line 164:

**Current (broken) — line 169:**
```python
deepest = min(confirmed, key=lambda cluster: cluster.get("bottom", float("inf")))
return float(deepest["bottom"]) * (1 - 0.003)
```

**Fixed:**
```python
# CORRECT — picks nearest support strictly below zone bottom
nearest = max(confirmed, key=lambda cluster: cluster.get("level", 0.0))
return float(nearest["bottom"]) * (1 - 0.003)
```

Also update the no-candidates fallback (line 168) from 3% to 0.5%:

```python
# BEFORE
if not confirmed:
    return entry_zone_bottom * 0.97      # 3% — too wide, was placeholder

# AFTER
if not confirmed:
    logger.warning(
        "sl_no_support_below_zone symbol=%s zone_bottom=%.8f — using 0.5pct fallback",
        symbol, entry_zone_bottom,
    )
    return entry_zone_bottom * 0.995     # 0.5% — tight fallback when no local support
```

### R2 — Stop Distance Validation Inside `_find_sl()`

After computing `sl`, validate bounds before returning:

```python
from bybit_bot.config import MIN_STOP_DIST_PCT, MAX_STOP_DIST_PCT  # 0.003, 0.08

stop_dist_pct = (entry_zone_bottom - sl) / entry_zone_bottom

if stop_dist_pct < MIN_STOP_DIST_PCT:
    logger.warning("sl_too_tight symbol=%s stop_pct=%.4f — widening to minimum",
                   symbol, stop_dist_pct)
    sl = entry_zone_bottom * (1 - MIN_STOP_DIST_PCT)
    stop_dist_pct = MIN_STOP_DIST_PCT

if stop_dist_pct > MAX_STOP_DIST_PCT:
    logger.warning("sl_too_wide symbol=%s stop_pct=%.4f — disqualified",
                   symbol, stop_dist_pct)
    return None   # get_sr_levels() must handle None return from _find_sl()
```

`MIN_STOP_DIST_PCT` and `MAX_STOP_DIST_PCT` must be read from `config.py`.
Do not hardcode values.

> **Note:** `_find_sl()` signature changes to return `float | None`. Update
> `get_sr_levels()` at line 220 to handle the None case:
> ```python
> sl_level = _find_sl(support_clusters, entry_bottom)
> if sl_level is None:
>     return _empty_result(symbol)
> ```

### R3 — Enrich `get_sr_levels()` Return Dict (Additive)

Add observability fields to the existing return dict in `get_sr_levels()`.
These are additive — no existing fields change:

```python
return {
    # ... all existing fields unchanged ...
    "sl_level":          sl_level,
    "stop_dist_pct":     ...,           # existing — keep as-is
    # NEW additive fields:
    "sl_fallback_used":  sl_fallback,   # bool — True if no candidates found
    "sl_candidates":     sl_candidates, # int — number of support clusters below zone
}
```

Pass `sl_fallback` and `sl_candidates` out of `_find_sl()` via a small
internal result dict, or compute them in `get_sr_levels()` directly.

### R4 — Exclude Far 1D Supports from Candidates (REQUIRED)

Before selecting nearest support, exclude clusters more than 15% below the
entry zone bottom. These are macro 1D supports irrelevant to short-term entries
and are exactly what caused this bug:

```python
MAX_SL_DEPTH_PCT = 0.15   # local constant in sr_calculator.py

confirmed = [
    cluster for cluster in support_clusters
    if cluster.get("top", 0.0) < entry_zone_bottom
    and (entry_zone_bottom - cluster.get("level", 0.0)) / entry_zone_bottom <= MAX_SL_DEPTH_PCT
]

# Fallback: if 15% filter removes all candidates, use unfiltered list
if not confirmed:
    confirmed = [
        cluster for cluster in support_clusters
        if cluster.get("top", 0.0) < entry_zone_bottom
    ]
    # Log that filter was bypassed
    if confirmed:
        logger.info("sl_depth_filter_bypassed symbol=%s — using unfiltered candidates", symbol)
```

R4 is **required** (not optional). It prevents the class of bug caused by
deep historical 1D supports re-entering the candidate pool.

## 8. Expected Outputs After Fix

Using 2026-09-23 live data as regression baseline:

| Symbol   | Zone Bottom | Expected SL   | Expected Stop % | Card Issued |
|----------|-------------|---------------|-----------------|-------------|
| LABUSDT  | $0.05930    | ~$0.05865     | ~1.0–1.7%       | ✅ YES      |
| MNTUSDT  | $0.66533    | ~$0.66122     | ~0.6–1.2%       | ✅ YES      |
| ONDOUSDT | $0.32150    | ~$0.31350     | ~2.5%           | ✅ YES      |

All three must pass the 8% stop guard.

## 9. Interfaces / Contracts

### `_find_sl()` Signature Change

```python
# BEFORE
def _find_sl(support_clusters: list[dict], entry_zone_bottom: float) -> float:

# AFTER
def _find_sl(support_clusters: list[dict], entry_zone_bottom: float, symbol: str = "") -> float | None:
```

`symbol` added for logging. Return `None` when stop is too wide after all
candidates are evaluated.

### Callers — No Changes Required

`planning.py` and `execution.py` call `get_sr_levels()` and read
`sr.get("sl_level")` and `sr.get("stop_dist_pct")`. These keys are
unchanged. `_find_sl()` is private and not called directly by any
module outside `sr_calculator.py`.

### Config constants used (read-only — do not modify values)

```python
MIN_STOP_DIST_PCT = 0.003   # 0.3% minimum stop distance
MAX_STOP_DIST_PCT = 0.08    # 8.0% maximum stop distance
```

## 10. Acceptance Criteria

- R1–R4 all implemented
- `_find_sl()` returns nearest-below-zone support, not global minimum
- LAB, MNT, ONDO all produce SL within 8% stop guard
- Fallback changed from 3% to 0.5% with warning log
- 15% depth filter active; bypass fallback logged when triggered
- `get_sr_levels()` handles `_find_sl()` returning `None` → `_empty_result()`
- `sl_fallback_used` and `sl_candidates` present in `get_sr_levels()` return
- Full test suite passes — zero regressions
- `MIN_STOP_DIST_PCT` and `MAX_STOP_DIST_PCT` sourced from config.py

## 11. Required Tests

Create `bybit_bot/tests/test_sl_algorithm.py`:

1. `test_sl_uses_nearest_support_below_zone` — supports [0.040, 0.058, 0.055, 0.045] with zone_bottom=0.0593 → sl_anchor nearest to 0.058
2. `test_sl_rejects_global_minimum` — same data → sl_anchor != 0.040
3. `test_sl_fallback_when_no_candidates` — all supports above zone_bottom → fallback_used=True, sl ≈ zone_bottom × 0.995
4. `test_sl_disqualifies_when_too_wide` — nearest support 12% below zone → return None from _find_sl
5. `test_sl_widens_when_too_tight` — nearest 0.1% below → sl widens to MIN_STOP_DIST_PCT
6. `test_sl_returns_float_or_none` — return type is float or None (not dict, not bare level)
7. `test_lab_regression` — LAB zone_bottom=0.0593, mock supports → sl between $0.0580 and $0.0592
8. `test_mnt_regression` — MNT zone_bottom=0.66533 → sl between $0.658 and $0.665
9. `test_sl_candidates_field_in_get_sr_levels` — `sl_candidates` key present in get_sr_levels() return
10. `test_r4_excludes_far_1d_supports` — support 20% below zone excluded; support 10% below included
11. `test_r4_bypass_fallback_when_all_far` — if ALL supports >15% below zone, bypass filter and use them
12. `test_get_sr_levels_handles_none_sl` — when _find_sl returns None, get_sr_levels returns _empty_result()

## 12. Expected Deliverables

- Modified: `bybit_bot/sr_calculator.py`
- New: `bybit_bot/tests/test_sl_algorithm.py`
- Completion report (Article 9 format) including LAB/MNT/ONDO SL values after fix

## 13. Failure / Escalation Conditions

STOP and escalate to CTO if:

- LAB or MNT still produce stop > 8% after fix (indicates deeper S/R data bug)
- Fix requires modifying `MIN_STOP_DIST_PCT` or `MAX_STOP_DIST_PCT` values
- `get_sr_levels()` callers in `planning.py` or `execution.py` break (they must not)
- `_find_sl()` None return path is not handled by `get_sr_levels()`

## 14. Completion Report Requirements

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list with line ranges]
Root Cause Confirmed: [exact broken line]
LAB SL After Fix: [$X.XXXXX | stop %: X.XX%]
MNT SL After Fix: [$X.XXXXX | stop %: X.XX%]
ONDO SL After Fix: [$X.XXXXX | stop %: X.XX%]
Fallback changed 3% → 0.5%: [YES / NO]
R4 (15% filter) implemented: [YES / NO]
Tests Run: [pytest output summary]
Tests Passed: [count]
Tests Failed: [count + names + errors]
Known Issues: [unresolved]
Recommended Next Step: CTO regression check → assign TASK-026
```

## 15. Review Plan

```
Codex implements → Sonnet quant review → CTO regression check (LAB/MNT/ONDO)
```

## 16. Skill Extraction Decision

No skill to be extracted.

## 17. Status / Sign-off

```
Status:         READY FOR IMPLEMENTATION — P0 CRITICAL
                Blocks all execution card generation
Assigned to:    Codex
CTO sign-off:   2026-09-23
Priority:       HIGHEST — implement before TASK-026
Gate:           Must complete and pass CTO regression check
                before TASK-026 implementation begins
```
