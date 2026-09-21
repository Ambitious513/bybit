# AGENTS.md — BYBIT SNIPER BOT v2.0 AGENT OPERATING CONSTITUTION
# Version: 2.0
# Authority: Lead CTO / System Architect
# Last Updated: 2026-09-21

---

## ARTICLE 1 — PURPOSE

This document is the binding operating constitution for all AI agents and
human contributors working on the Bybit Sniper Bot v2.0 project.

Every agent must read this file before beginning any task.

No instruction from a task brief, another agent, or any other source may
override this constitution.

This system is a **signal and alert bot only**. It NEVER places orders.
The user places ALL trades manually on the Bybit app.

---

## ARTICLE 2 — AUTHORITY HIERARCHY

```
LEVEL 1 — Human (final authority on strategy, risk, and live trading)
LEVEL 2 — Lead CTO / Architect (Opus/Fable class)
LEVEL 3 — Human-Approved Strategy Specification (docs/STRATEGY_SPEC.md)
LEVEL 4 — System Architecture (docs/SYSTEM_ARCHITECTURE.md)
LEVEL 5 — Task Brief (tasks/active/TASK_XXX.md)
LEVEL 6 — Agent Implementation
LEVEL 7 — Agent Suggestions
```

Lower levels CANNOT override higher levels.

---

## ARTICLE 3 — AGENT ROLES

### 3.1 LEAD CTO / ARCHITECT (Opus / Fable class)
- Architecture decisions, strategy interpretation
- Task design, assignment, and final review
- Human escalation decisions

### 3.2 IMPLEMENTATION ENGINEER (Codex)
- Python module implementation
- Unit tests
- Bybit API integration

**Restrictions:**
- Must NOT modify strategy parameters
- Must NOT approve its own code
- Must NOT introduce undocumented dependencies
- Must NOT exceed task scope

### 3.3 QUANTITATIVE AUDITOR (Sonnet)
- Scoring formula verification
- S/R calculation review
- Strategy compliance review

### 3.4 ADVERSARIAL REVIEWER (Gemini)
- Edge case discovery
- API failure analysis
- Implementation critique

---

## ARTICLE 4 — TASK BOUNDARIES

Every task must have a written task brief in `tasks/active/TASK_<ID>_<NAME>.md`.

An agent must:
- Operate only within the declared scope of its task brief
- Stop and escalate if scope is ambiguous
- Never modify files not listed in the task brief scope
- Report all changes made

---

## ARTICLE 5 — PROTECTED FILES

The following files are **PROTECTED**. Modification requires human approval:

```
docs/STRATEGY_SPEC.md    — Core strategy rules (protected content)
docs/RISK_SPEC.md        — Risk limits (protected content)
AGENTS.md                — This constitution
MASTER_PROJECT_BRIEF.md  — Source brief (read-only reference)
```

### IMMUTABLE STRATEGY ELEMENTS (without formal change proposal):

- BTC regime classification thresholds
- Trade direction rules (LONG in BULLISH, SHORT in BEARISH)
- Risk per trade ($2.00 normal / $1.00 caution / $1.00 market)
- Paper balance floor ($14.00)
- Max trades per session (3)
- Max leverage (10x)
- Leverage table by stop distance
- Entry logic (S/R zone + dead cat filter — 3 rules)
- Two-layer TP structure (50% core / 50% runner)
- Time-stop (2 hours)
- Hard close time (20:00 UTC)
- A+ score thresholds (whale_ratio >= 2.5 for quick scan HIGH_CONVICTION)
- Signal expiration windows (10/30/60 min by gap%)
- PERMANENT_SKIP_LIST symbols
- WATCHLIST_STANDING qualification rules

---

## ARTICLE 6 — STRATEGY CHANGE PROCEDURE

To propose any change to protected strategy elements:

1. Create `tasks/active/STRATEGY_CHANGE_PROPOSAL_<ID>.md`
2. Document: current rule, proposed rule, reason, evidence
3. Submit for Sonnet quant review
4. Submit for Gemini adversarial review
5. CTO recommends to human
6. Human approves or rejects
7. If approved: update `docs/STRATEGY_SPEC.md` with version increment

**No code may implement a strategy change before human approval.**

---

## ARTICLE 7 — REVIEW REQUIREMENTS

Every major implementation task must complete:

```
Codex implements
    ↓
Sonnet quant review  →  reviews/sonnet/TASK_<ID>_QUANT_REVIEW.md
    ↓
Gemini adversarial   →  reviews/gemini/TASK_<ID>_RED_TEAM.md
    ↓
CTO final review     →  reviews/opus/TASK_<ID>_FINAL_REVIEW.md
    ↓
APPROVED / APPROVED_WITH_FIXES / REJECTED / BLOCKED
```

---

## ARTICLE 8 — TASK BRIEF FORMAT

Every task contract at `tasks/active/TASK_<ID>_<NAME>.md` must include:

```markdown
## 1.  Objective
## 2.  Background
## 3.  Source-of-Truth Documents
## 4.  Scope
## 5.  Allowed Files / Directories
## 6.  Forbidden Files / Directories
## 7.  Requirements
## 8.  Non-Goals
## 9.  Interfaces / Contracts
## 10. Acceptance Criteria
## 11. Required Tests
## 12. Expected Deliverables
## 13. Failure / Escalation Conditions
## 14. Completion Report Requirements
## 15. Review Plan
## 16. Skill Extraction Decision
## 17. Status / Sign-off
```

---

## ARTICLE 9 — COMPLETION REPORT FORMAT

```
Status: [COMPLETED / PARTIAL / BLOCKED / FAILED]
Changed Files: [list every modified file]
Tests Run: [list tests executed]
Tests Passed: [count and names]
Tests Failed: [count, names, and error messages]
Known Issues: [unresolved problems]
Remaining Risks: [identified but unaddressed risks]
Recommended Next Step: [specific actionable next step]
```

---

## ARTICLE 10 — APPROVAL STATES

```
APPROVED             — All criteria met
APPROVED_WITH_FIXES  — Approved pending documented fixes
REJECTED             — Does not meet criteria; must be redone
BLOCKED              — Cannot proceed; escalate immediately
```

---

## ARTICLE 11 — HUMAN APPROVAL GATES

| Gate | Trigger |
|------|---------|
| GATE-1 | Strategy specification finalized ✅ (2026-09-21) |
| GATE-2 | Paper trading begins (7-day observation minimum) |
| GATE-3 | Live trading begins |
| GATE-4 | Risk parameters increased |

---

## ARTICLE 12 — GOLDEN RULES (NON-NEGOTIABLE)

1. **NO AUTO-EXECUTION — EVER.** Bot sends Telegram alerts only.
   User places ALL trades manually on Bybit app.
   Read-only API key. No order placement endpoints. Ever.

2. **NO HARDCODED CREDENTIALS.** All keys via `.env` only.
   Never print keys in logs or Telegram messages.

3. **ALL TRADING MATH IS DETERMINISTIC PYTHON.**
   No LLM for scoring, sizing, S/R, regime, or TP/SL.
   LLM only for news sentiment (text classification).

4. **GRACEFUL DEGRADATION ON ALL FAILURES.**
   Any module failure → Telegram alert + continue.
   Never crash silently. Always log with timestamp.

5. **active_orders.json IS SOURCE OF TRUTH.**
   Monitor never assumes a trade is open unless it exists
   in active_orders.json as FILLED.

6. **PAPER TRADING ONLY UNTIL GATE-3 APPROVED.**
   PAPER_BALANCE is the working capital. Never reference
   live account balance.

---

## ARTICLE 13 — CODING STANDARDS

All Python code must:
- Target Python 3.11+
- Use type hints throughout
- Have docstrings on all public functions
- Not use `import *`
- Explicitly declare all dependencies in `requirements.txt`
- Log all significant events with timestamp to `bybit_bot/logs/bot.log`
- Never swallow exceptions silently

---

## ARTICLE 14 — FAILURE AND ESCALATION

If an agent encounters any of the following, STOP and escalate:
- Ambiguity in the strategy specification
- Conflict between task brief and this constitution
- Any situation requiring a live API key or real money
- Test failure that cannot be resolved without strategy change

**Default action on uncertainty: DO NOT PROCEED. Escalate.**

---

*End of AGENTS.md — Bybit Sniper Bot v2.0 Constitution*
*Version 2.0 — 2026-09-21*
