# CTO Full Review — Bybit Sniper Bot v2.0
**Reviewer:** Lead CTO / Systems Architect
**Date:** 2026-09-23
**Scope:** Post-GATE-2 team review analysis, TASK-026/027 approval, GATE-3 readiness assessment
**Trigger:** Team full system review (BOT_V2_FULL_SYSTEM_REVIEW.md) + TASK-026 and TASK-027 proposals

---

## 1. Overall System Assessment

The team review grade of **A** is accurate. The detailed breakdown:

| Area | Grade | Rationale |
|---|---|---|
| Architecture | A+ | APScheduler + shared mutex + atomic writes + daemon poll = production-grade concurrent system |
| Test coverage | A+ | 133 tests, real-thread concurrency test, zero mocks for lock verification |
| Governance model | A+ | Gate system, protected strategy files, formal SCP process, full review pipeline |
| Strategy accuracy | A | Scoring logic correct; one S/R algorithm bug found (TASK-027) |
| Pre-GATE-3 gaps | Minor | All fixable; one is P0 (blocking) |

The callouts in the team review are precise and technically accurate. The eight "exceptional" items (shared `_ORDERS_LOCK`, atomic writes, real-thread test, gate governance, fail-fast guards, Telegram offset persistence, research mutex, 20:05 cache-only run) are the exact decisions that separate this from a typical retail bot.

---

## 2. Critical Finding: TASK-027 — SL Algorithm Bug (P0)

### Bug Confirmed — Root Cause Identified

I read `sr_calculator.py` in full. The bug is at **line 169**:

```python
# BROKEN — picks the lowest support on the entire chart
deepest = min(confirmed, key=lambda cluster: cluster.get("bottom", float("inf")))
return float(deepest["bottom"]) * (1 - 0.003)
```

`min()` with key `"bottom"` selects the cluster with the **lowest absolute price** across all timeframes — including 1D Fibonacci bottoms and 1D Volume Profile lows that are 30–40% below the current trading range. The stop guard rejects all of these as "too wide," which is why every `/deepdive` returns "stop too wide — skip."

**Live data confirmation:**

| Symbol | Zone Bottom | Current SL (broken) | Stop % | Correct SL | Correct % |
|---|---|---|---|---|---|
| LABUSDT | \$0.05930 | \$0.03639 | 38.7% | \~\$0.05865 | 1.0% |
| MNTUSDT | \$0.66533 | \$0.42113 | 36.7% | \~\$0.66122 | 0.6% |
| ONDOUSDT | \$0.32150 | \$0.29252 | 9.0% | \~\$0.31350 | 2.5% |

### The Fix — One Line

```python
# FIXED — picks the NEAREST (highest) support cluster strictly below entry zone
nearest = max(confirmed, key=lambda cluster: cluster.get("level", 0.0))
return float(nearest["bottom"]) * (1 - 0.003)
```

### Three Corrections to TASK-027 Contract (Applied in This Report)

**Correction 1 — Function name.** The contract references `get_sl_level()`. The actual function is `_find_sl()` (private helper, line 164). There is no `get_sl_level()`. Callers (`planning.py`, `execution.py`) access SL via the public `get_sr_levels()` dict which already returns `sl_level` and `stop_dist_pct` — **no caller updates required**. R3 dict enrichment (`candidates_used`, `fallback_used`) should be added to the `get_sr_levels()` return dict as additive fields.

**Correction 2 — Fallback wording.** The existing fallback (line 168) returns `entry_zone_bottom * 0.97` (3%). The contract proposes changing this to `entry_zone_bottom * 0.995` (0.5%). This is a behaviour change that must be documented explicitly. The 0.5% fallback is correct for near-zone situations with no local support — it prevents an unreasonably wide SL when the zone itself is the lowest nearby structure.

**Correction 3 — R4 is required, not optional.** The 15% filter (excluding supports >15% below zone bottom from the candidate list) is what prevents this category of bug from recurring as price moves. 1D macro supports are valid for long-term analysis but are not appropriate as short-term entry stops. R4 must be implemented alongside R1–R3.

### TASK-027 CTO Decision: **APPROVED — P0, IMPLEMENT FIRST**

---

## 3. TASK-026 — Pre-GATE-3 Hardening (Approved with Amendments)

The TASK-026 contract is well-structured, correctly scoped, and addresses the right set of pre-GATE-3 issues. Six requirements, 14 tests, full review pipeline.

### Four Amendments Applied

**Amendment 1 — Dead Cat Phase 1 guard: `< 5` → `< 6`**

```python
# BEFORE (contract as written)
if not candles or len(candles) < 5:

# AFTER (corrected)
if not candles or len(candles) < 6:
```

With exactly 5 candles, `candles[-6:-1]` silently returns 4 baseline candles (Python slicing out of bounds clips to 0). The implementation needs 5 baseline candles + 1 bounce candle = minimum 6.

**Amendment 2 — SMTP credentials must load from `.env` via `os.getenv()`**

The contract proposes empty string literals in `config.py`:
```python
SMTP_USER = ""       # wrong — hardcoded literal
SMTP_PASSWORD = ""   # wrong
```

The correct pattern, consistent with all existing config keys:
```python
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM    = os.getenv("EMAIL_FROM", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
```

Credentials must never appear in source files. The operator adds them to `.env` before GATE-3.

**Amendment 3 — Neutral drop volume test must cover both sub-cases**

`test_dead_cat_phase1_neutral_vol_passes_to_phase2` (drop_vol_ratio=1.0) must verify:
- Sub-case A: ratio=1.0 + clean Phase 2 → `passed=True`
- Sub-case B: ratio=1.0 + failed Phase 2 → `passed=False`

As written, the test name is ambiguous about which outcome is expected. Both must be explicit.

**Amendment 4 — Sequencing constraint with TASK-027**

Both TASK-026 (R1 — Dead Cat) and TASK-027 modify `sr_calculator.py`. They cannot run concurrently. **TASK-026 implementation must not begin until TASK-027 is APPROVED by CTO.** Codex must read TASK-027 completion status before starting TASK-026 R1.

### TASK-026 CTO Decision: **APPROVED with 4 amendments — Begin AFTER TASK-027 APPROVED**

---

## 4. Additional Team Findings — CTO Disposition

| Finding | Decision | Rationale |
|---|---|---|
| Funding creep alert | ✅ Included in TASK-026 R6 | Correct — post-fill monitoring gap |
| Dead cat Phase 1 (drop vol) | ✅ Included in TASK-026 R1 | P1, BEAT example confirmed live |
| Multi-session whale tracking | 📋 TASK-028 candidate | Post-GATE-3 enhancement |
| IV rank in card header | 📋 TASK-029 candidate | Display enhancement, non-blocking |
| Negative funding bonus (WIF) | 🚨 Requires formal SCP | Strategy scoring change — not TASK-026 scope |

**Negative funding bonus note:** A bonus for negative funding on bullish setups is a scoring weight change. Per AGENTS.md Article 6, this requires a Strategy Change Proposal with evidence, quant review, adversarial review, and human approval. It cannot be included in TASK-026.

---

## 5. Implementation Sequence — Pre-GATE-3

```
NOW       TASK-027 — SL Algorithm Fix (P0)
           sr_calculator.py: _find_sl() fix + 15% filter + enriched return
           Time: 1 day | Reviewer: Sonnet quant + CTO regression check
              ↓
AFTER 027  TASK-026 — Pre-GATE-3 Hardening (P1/P2)
           6 requirements, 14 tests
           Time: 2–3 days | Full review pipeline
              ↓
OPERATOR   Email fallback setup + /test_email confirmation
           Hard prerequisite: must be operator-tested before GATE-3
              ↓
GATE-3     Human approval decision
```

---

## 6. GATE-2 Observation Checklist

The 7-day paper observation started 2026-09-22. Items to verify before GATE-3:

| Criterion | Status |
|---|---|
| 7 consecutive days uptime | ⏳ Day 1 — check daily via heartbeat |
| No data corruption events | ✅ Day 1 clean |
| No missed hard closes | ✅ Day 1 clean |
| Execution cards match manual analysis | ⚠️ Blocked by TASK-027 SL bug — fix first |
| Monitor correctly alerts TP/SL | ✅ Logic correct, no open orders to test yet |
| Research fires on schedule | ✅ Confirmed via logs |
| Telegram offset survives restart | ✅ tg_offset.json present and updated |

---

## 7. GATE-3 Prerequisites (Complete Before Human Approval)

| # | Item | Task | Operator Action Required |
|---|---|---|---|
| 1 | SL algorithm fix | TASK-027 | None |
| 2 | Dead cat Phase 1 | TASK-026 R1 | None |
| 3 | `/filled` price validation | TASK-026 R2 | None |
| 4 | TRADFI perps audit | TASK-026 R3 | Review Telegram warning on next restart |
| 5 | Email fallback | TASK-026 R4 | **Must personally test `/test_email` and confirm receipt** |
| 6 | Connection pool fix | TASK-026 R5 | None |
| 7 | Funding creep alert | TASK-026 R6 | None |
| 8 | 7-day paper observation | GATE-2 | **Human approves GATE-3 after reviewing paper results** |

---

## 8. Deferred Items (Post-GATE-3)

| Item | Future Task | Priority |
|---|---|---|
| Multi-session whale tracking | TASK-028 | P2 |
| IV rank in card header | TASK-029 | P3 |
| `btc_support` precision (true lowest S/R) | TASK-026/future | P3 |
| Negative funding bonus for squeeze setups | SCP-002 (pending evidence) | P2 |

---

## 9. Summary

The system is production-grade in architecture and governance. The one critical pre-GATE-3 fix (TASK-027 SL algorithm) is a one-line change that will immediately unlock card generation for LAB, MNT, ONDO, and all other setups with valid near-zone stops. TASK-026 addresses the remaining hardening items. Both tasks are approved with documented amendments.

**System is NOT GATE-3 ready until TASK-027 and TASK-026 are both APPROVED.**

---
*CTO sign-off: 2026-09-23*
*Next action: Codex implements TASK-027 → CTO regression check → Codex implements TASK-026 → Full pipeline → GATE-3 decision*
