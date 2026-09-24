# Codex Analysis — Repo Audit 2026-09-24

## Scope and evidence

This is a read-only analysis of the audit supplied on 2026-09-24, checked
against repository commit `e33d7d5`.

- Local `master` and `origin/master` matched at `e33d7d5` when reviewed.
- Working tree was clean.
- `python -m pytest bybit_bot/tests -q` passed: **160 passed**.
- `python -m compileall -q bybit_bot` passed.
- No order-placement endpoint was found in bot code. Telegram and OpenRouter
  are the only modules using HTTP POST.

## Audit finding assessment

| Audit item | Assessment | Evidence / conclusion |
|---|---|---|
| TASK-026 and TASK-027 landed | Confirmed technically | Both task contracts, implementation commits, CTO reviews, and their test coverage exist. The suite passes 160/160. |
| Issue 1: no SHORT pipeline | Confirmed, but understated | BTC can classify `BEARISH` with direction `SHORT`, but candidate qualification is long-only. More critically, S/R creates support-below-price geometry and `execution.generate_card()` rejects that geometry for a SHORT. The execution-card checklist is also long-specific. |
| Issue 2: BREAKOUT subtype absent | Confirmed | `classify_btc_regime()` does not produce `BREAKOUT`. |
| Issue 3: watchlist `note` KeyError | Rejected | All current `WATCHLIST_STANDING` entries in `config.py` include `note`; the direct lookup in `screener.get_standing_watchlist()` is safe for the current source-of-truth config. |
| Issue 4: watchlist `skip_if` not enforced | Confirmed, with a caveat | `qualify_coins()` evaluates `qualify_if` only. The suggested direct patch is incomplete because the current parser supports `AND` only, while immutable rules include `OR` and natural-language price-change conditions. |

## Detailed findings

### F-01 — SHORT eligibility is not end-to-end executable

Severity: P0 functional/specification gap.

The strategy specification explicitly makes SHORT setups eligible in BEARISH
regimes. The current pipeline does not turn that eligibility into a valid
manual card:

1. `screener.py` has GAINER and BULLISH-only LOSER sourcing; it has no short
   candidate source.
2. `research.qualify_coins()` only qualifies GAINER/LOSER candidates in a
   BULLISH regime; its TradFi criterion is also Bullish-only.
3. `sr_calculator.get_sr_levels()` selects a support zone below current price
   and a stop below entry, which is valid LONG geometry only.
4. `execution.generate_card()` correctly rejects a SHORT whose stop is below
   entry, so a BEARISH regime cannot create a valid card with current S/R
   output.
5. The human checklist uses LONG-only conditions (green candle, above
   midpoint, BTC holding above support).

The audit's proposed candidate thresholds and reversed scoring are not safe
to implement directly: they are new strategy parameters. The protected
strategy specification does not define a SHORT candidate filter, SHORT S/R
entry rule, SHORT stop model, or SHORT checklist.

### F-02 — BREAKOUT is specified but not representable by current data

Severity: P1 strategy implementation gap.

The strategy defines a BULLISH BREAKOUT as price above both 4H and 1D
resistance. Current S/R output exposes only a combined nearest-resistance
list; it does not identify a 4H resistance and a 1D resistance independently.
Therefore the audit's suggested `resistances[0]` implementation cannot verify
the required condition.

The task also needs CTO clarification of precedence when STRONG, PULLBACK,
and BREAKOUT conditions overlap. Current code lets PULLBACK overwrite STRONG;
the specification lists subtypes but does not define precedence.

### F-03 — Watchlist hard exclusions are not executed

Severity: P1 specification-compliance gap.

The current watchlist flow evaluates `qualify_if` and ignores `skip_if`.
This matters most for conditions that can coexist with qualification, such as
TAO's price-run rule and WIF's daily-change rule.

The condition parser cannot yet safely evaluate every immutable `skip_if`:

- LINK uses `OR`.
- TAO uses `price already ran >8% on the day`.
- WIF uses `daily_change > 15%`.

A task must add deterministic support for the exact existing condition grammar
and tests for each watchlist entry before using `not skip` as a gate. It must
not reinterpret or alter the protected rules.

### F-04 — TASK-026/027 review artifacts are incomplete

Severity: P1 governance gap.

The repository contains CTO final reviews for TASK-026 and TASK-027, but no
corresponding Sonnet quant or Gemini red-team review files. AGENTS.md requires
both review stages before CTO final approval for major implementation tasks.
This is a documentation/process gap, not a detected code failure.

## Recommended implementation plan

### Phase 0 — Complete governance records

1. Obtain and commit Sonnet and Gemini reviews for TASK-026 and TASK-027.
2. Reconcile their findings with the existing CTO final reviews; if either
   identifies a material defect, create a bounded corrective task rather than
   modifying approved work ad hoc.

### Phase 1 — Formal SHORT strategy specification

Create a strategy-change proposal before implementation. It must define:

- candidate universe and qualification thresholds;
- deterministic short scoring behavior;
- resistance-based entry-zone and stop-loss geometry;
- bearish equivalent of the pre-entry/dead-cat checklist;
- BTC invalidation and monitoring semantics for a SHORT;
- test vectors for valid/invalid geometry and BEARISH candidate flow.

Route it through Sonnet, Gemini, CTO, and human approval as required by
AGENTS.md. Do not implement the audit's suggested numeric thresholds before
that approval.

### Phase 2 — TASK-028: Direction-aware SHORT pipeline

Begin only after the Phase 1 approval. Scope should cover the candidate
screener, research qualification, deterministic S/R output, planning,
execution-card presentation, and monitor behavior as needed by the approved
short rules.

Acceptance tests should prove that:

- a valid BEARISH market produces one valid SHORT card;
- LONG paths remain unchanged;
- a SHORT stop is above entry and TP levels are below entry;
- invalid short geometry is rejected;
- no order-placement endpoint is introduced.

### Phase 3 — TASK-029: BREAKOUT subtype

Add a typed S/R interface that exposes independently validated 4H and 1D
resistance levels. Update regime classification only after CTO specifies
subtype precedence. Tests must cover non-breakout, 4H-only, 1D-only, both,
and overlap-precedence cases.

### Phase 4 — TASK-030: Watchlist `skip_if` enforcement

Extend the deterministic condition evaluator for the exact existing immutable
grammar: `AND`, `OR`, numeric comparisons, `daily_change`, and the TAO price
run phrase. Qualify a watchlist instrument only when `qualify_if` passes and
`skip_if` does not. Add a table-driven test for every configured watchlist
symbol and boundary condition.

### Release gate

Do not treat any future implementation as live-trading authorization. Maintain
paper-only operation until the human grants GATE-3 after all task reviews,
operator checks, and the required observation period are complete.

