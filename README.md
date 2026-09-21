# Bybit Sniper Bot v2.0

Signal and alert bot for Bybit perpetual futures. Identifies high-conviction
momentum setups via capital flow metrics, scores them deterministically, and
delivers formatted execution cards to Telegram.

**The bot NEVER places orders. User executes all trades manually.**

## Governance

| Document | Purpose |
|---|---|
| `AGENTS.md` | Agent operating constitution |
| `docs/STRATEGY_SPEC.md` | Strategy rules (GATE-1 locked) |
| `docs/RISK_SPEC.md` | Risk parameters (GATE-1 locked) |
| `tasks/active/` | Task contracts for each build phase |

## Build Phases

| Phase | Task | Status |
|---|---|---|
| 1 | TASK-016 — Foundation | PENDING |
| 2 | TASK-017 — Research Engine | PENDING |
| 3 | TASK-018 — S/R Calculator + Planning | PENDING |
| 4 | TASK-019 — Execution Cards | PENDING |
| 5 | TASK-020 — Monitor Core | PENDING |
| 5B | TASK-021 — Quick Scan + Deep Dive | PENDING |
| 6 | TASK-022 — Advanced Monitor + Commands | PENDING |

## Quick Start (after Phase 1 complete)

```bash
cp .env.example .env
# fill in API keys
pip install -r bybit_bot/requirements.txt
python bybit_bot/orchestrator.py --test
```

## Gates

- **GATE-1** ✅ Strategy + Risk spec approved (2026-09-21)
- **GATE-2** 🔒 Paper trading — 7-day observation minimum
- **GATE-3** 🔒 Live trading — human approval required
