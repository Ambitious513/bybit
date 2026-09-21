# TASK-016 — Phase 1: Foundation Infrastructure
## Status: PENDING | Assigned to: Codex (Implementation Engineer)

---

## 1. Objective
Build the complete foundation layer for the Bybit Sniper Bot v2.0.
Six files: config.py, bybit_api.py, data_aggregator.py, openrouter.py,
telegram.py, orchestrator.py. On completion, `python orchestrator.py --test`
must deliver "Bot online" to Telegram and print live BTC price to console.

## 2. Background
The A+ Scanner v1.x produced zero signals in real-world conditions because
it targeted SHORT exhaustion patterns in BEARISH BTC — a rare regime. v2.0
pivots to capital-flow-driven LONG momentum plays in BULLISH BTC. This task
builds the foundation all subsequent phases depend on.
All new code lives in `bybit_bot/` within the existing repo.

## 3. Source-of-Truth Documents
- `AGENTS.md` — operating constitution (binding)
- `MASTER_PROJECT_BRIEF.md` — source spec (read-only)
- `docs/STRATEGY_SPEC.md` — v2.0 strategy rules
- This task contract (supersedes any prior verbal instruction)

## 4. Scope
Create the following NEW files inside `bybit_bot/`:
  config.py, bybit_api.py, data_aggregator.py, openrouter.py,
  telegram.py, orchestrator.py, requirements.txt, .env.example,
  data/.gitkeep, logs/.gitkeep

## 5. Allowed Files / Directories
- `bybit_bot/` — all new files in this directory only
- `bybit_bot/data/` — JSON state files
- `bybit_bot/logs/` — log output

## 6. Forbidden Files / Directories
- `src/scanner/` — DO NOT TOUCH existing scanner
- `tests/` — DO NOT TOUCH existing tests
- `docs/STRATEGY_SPEC.md`, `AGENTS.md` — protected, read-only
- `.env` (root) — DO NOT MODIFY

## 7. Requirements

### 7.1 config.py
- Load all values from `.env` via python-dotenv
- Define ALL constants from the spec verbatim:
  PAPER_BALANCE=18.66, PAPER_RISK_PER_TRADE=2.00, PAPER_RISK_CAUTION=1.00,
  PAPER_RISK_MARKET=1.00, PAPER_BALANCE_FLOOR=14.00
  MAX_LEVERAGE=10, CAUTION_MAX_LEVERAGE=5, MAX_TRADES_PER_SESSION=3
  TIME_STOP_HOURS=2, HARD_CLOSE_UTC_HOUR=20
  CORE_PCT=0.50, RUNNER_PCT=0.50
  WINDOW_URGENT_MINS=10, WINDOW_PATIENT_MINS=30, WINDOW_SET_FORGET_MINS=60
  MAX_CHASE_PCT=3.0
  BTC_BULL_WHALE_MIN=1.05, BTC_BEAR_WHALE_MAX=0.95, BTC_STRONG_MIN=1.30
  BTC_INVALIDATION=84200
  OPENROUTER_MAX_PROMPT_TOKENS=800, OPENROUTER_MAX_RESPONSE_TOKENS=400
- Define PERMANENT_SKIP_LIST (10 symbols from spec)
- Define TRADFI_PERPS list (4 symbols)
- Define WATCHLIST_STANDING dict (7 coins with qualify_if/skip_if/edge/note)
- Define SESSION_SKIP_LIST = [] (mutable, runtime only)
- OPENROUTER_KEYS list of 3 from env
- OPENROUTER_MODELS list of 3 (qwen first, gemma second, mistral third)

### 7.2 bybit_api.py
Implement ONLY these 6 functions (sync, requests library):
- `get_ticker(symbol: str) -> dict | None`
  Endpoint: GET /v5/market/tickers?category=linear&symbol={symbol}
  Returns: {price, price24hPcnt, turnover24h, high24h, low24h}
- `get_all_linear_tickers() -> list[dict]`
  Endpoint: GET /v5/market/tickers?category=linear
  Returns list of all tickers with same fields
- `get_klines(symbol: str, interval: str, limit: int) -> list`
  Endpoint: GET /v5/market/kline
  Returns: [[ts, open, high, low, close, volume, turnover], ...] oldest-first
- `get_long_short_ratio(symbol: str, period: str = "1h") -> dict | None`
  Endpoint: GET /v5/market/account-ratio?category=linear&symbol={symbol}&period={period}&limit=1
  Returns: {buyRatio, sellRatio, timestamp}
- `get_top_trader_ratio(symbol: str, period: str = "1h") -> dict | None`
  Endpoint: GET /v5/market/account-ratio?category=linear&symbol={symbol}&period={period}&limit=1&type=topTraderAccount
  Returns: {buyRatio, sellRatio}
- `get_funding_rate(symbol: str) -> dict | None`
  Endpoint: GET /v5/market/funding/history?category=linear&symbol={symbol}&limit=1
  Returns: {fundingRate, fundingRateTimestamp}
- `get_open_interest(symbol: str) -> dict | None`
  Endpoint: GET /v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h&limit=1
  Returns: {openInterest, timestamp}

Rate limiting: time.sleep(0.5) between sequential calls.
Retry logic: 3x on 429 (2s backoff), 2x on 500 (1s backoff).
Return None on all other errors — never raise to caller.
Log all errors to logs/bot.log with timestamp.
No auth required (all public market endpoints).

### 7.3 data_aggregator.py (NEW FILE — architectural addition)
This is the abstraction layer between bybit_api.py and research.py.
Implements ONE function:
- `get_capital_flow(symbol: str) -> dict | None`
  Calls 4 bybit_api functions concurrently via ThreadPoolExecutor:
    get_long_short_ratio(symbol)
    get_top_trader_ratio(symbol)
    get_funding_rate(symbol)
    get_open_interest(symbol)
  Derives:
    whale_ratio = buyRatio / sellRatio (long_short_ratio result)
    fund_side = "Bullish" if float(fundingRate) >= 0 else "Bearish"
    top_trader_ratio = top_trader buyRatio / sellRatio
  Returns normalized dict:
  {
    "longShortRatio": float,          # whale_ratio
    "fundSide": "Bullish"|"Bearish",
    "fundingRate": {"latest": float},
    "topTraderPositionRate": float,
    "openInterestHistory": {
      "current": float,               # from get_open_interest
      "thirtyDaysAgo": float | None   # None until oi_history.json has 30d data
    }
  }
  Load/append OI snapshot to data/oi_history.json on each call.
  For thirtyDaysAgo: find entry in oi_history.json with timestamp
  closest to (now - 30 days). Return None if < 30 days of data.
  Return None if any required sub-call fails.

### 7.4 openrouter.py
- Module-level current_key_index = 0
- Round-robin key rotation on every call
- On 429/quota → increment index → retry immediately
- On all keys exhausted → log warning → return None
- Model fallback: OPENROUTER_MODELS[0] → [1] → [2]
- `complete(prompt: str, system_prompt: str = "") -> str | None`
  POST https://openrouter.ai/api/v1/chat/completions
  Headers: Authorization: Bearer {key}, Content-Type: application/json
  Enforce OPENROUTER_MAX_RESPONSE_TOKENS
  Return string response or None
  Log which model/key was used each call

### 7.5 telegram.py
- `send_message(text: str) -> bool`
  POST https://api.telegram.org/bot{TOKEN}/sendMessage
  parse_mode: HTML, max 4096 chars, auto-truncate with "...[truncated]"
  Never raises, returns True/False
- `send_card(lines: list[str]) -> bool`
  Wraps in <pre> tags, calls send_message
  Same length limit

### 7.6 orchestrator.py
CLI interface:
- `--test`: send "Bot online: BTC $[price]" to Telegram + print to console
- `--research`: import and run research.run_research() (stub if research.py not yet built)
- `--plan`: import and run planning.run_planning() (stub)
- `--monitor`: import and run monitor.run_monitor_cycle() (stub)
- `--daemon`: start APScheduler with:
    Research job: every 4H (interval trigger, hours=4)
    Monitor job: every 2M (interval trigger, minutes=2)
    BTC check job: every 15M (interval trigger, minutes=15)
    Quick scan job: every 30M (interval trigger, minutes=30)
  Startup sequence (must all pass before scheduler starts):
    1. Validate all required env vars present (BYBIT_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    2. get_ticker("BTCUSDT") — if None → send Telegram error + exit
    3. send_message("Bot online: BTC $[price]") — if False → log error + exit
    4. openrouter.complete("ping") — if None → log warning (non-fatal, continue)
    5. Start scheduler
  Graceful degradation: if openrouter fails → log "no_llm_mode" → continue

### 7.7 requirements.txt
requests==2.31.0
python-dotenv==1.0.0
APScheduler==3.10.4
python-telegram-bot==20.7
numpy==1.26.0

## 8. Non-Goals
- Do NOT implement screener.py, research.py, sr_calculator.py, planning.py,
  execution.py, monitor.py, quickscan.py — those are later tasks
- Do NOT place any orders or reference order placement endpoints
- Do NOT use SQLAlchemy or aiohttp — sync requests only for new system
- Do NOT modify any existing src/scanner/ files

## 9. Interfaces / Contracts
All downstream tasks depend on these exact return shapes:

bybit_api.get_ticker() returns:
  {"price": str, "price24hPcnt": str, "turnover24h": str,
   "high24h": str, "low24h": str}

data_aggregator.get_capital_flow() returns:
  {"longShortRatio": float, "fundSide": str,
   "fundingRate": {"latest": float},
   "topTraderPositionRate": float,
   "openInterestHistory": {"current": float, "thirtyDaysAgo": float|None}}

telegram.send_card(lines) accepts list[str], each line is one row.

orchestrator daemon schedules: research(4H), monitor(2M), btc_check(15M), quickscan(30M)

## 10. Acceptance Criteria
- [ ] `python bybit_bot/orchestrator.py --test` sends "Bot online" Telegram message
- [ ] BTC price prints to console (non-zero, non-None)
- [ ] `data_aggregator.get_capital_flow("BTCUSDT")` returns valid dict with all 5 keys
- [ ] `openrouter.complete("Say hello")` returns a string (or None if keys invalid)
- [ ] `telegram.send_card(["line1", "line2"])` returns True
- [ ] OI history file `bybit_bot/data/oi_history.json` is created and appended on each call
- [ ] All errors logged to `bybit_bot/logs/bot.log` with timestamp
- [ ] No credentials appear in any log output

## 11. Required Tests
File: `bybit_bot/tests/test_foundation.py`
- test_get_ticker_returns_valid_shape — mock HTTP, assert keys present
- test_get_capital_flow_derives_fund_side_bullish — funding >= 0 → Bullish
- test_get_capital_flow_derives_fund_side_bearish — funding < 0 → Bearish
- test_whale_ratio_calculation — buyRatio=0.6, sellRatio=0.4 → ratio=1.5
- test_oi_history_accumulates — two calls produce two entries in oi_history.json
- test_telegram_send_message_truncates — string > 4096 chars gets truncated
- test_openrouter_key_rotation — mock 429 on key1 → automatically retries with key2
- test_bybit_api_returns_none_on_error — 500 response → None returned, no raise
- test_orchestrator_startup_exits_on_bybit_fail — if get_ticker returns None → sys.exit

## 12. Expected Deliverables
- bybit_bot/config.py
- bybit_bot/bybit_api.py
- bybit_bot/data_aggregator.py
- bybit_bot/openrouter.py
- bybit_bot/telegram.py
- bybit_bot/orchestrator.py
- bybit_bot/requirements.txt
- bybit_bot/.env.example
- bybit_bot/data/.gitkeep
- bybit_bot/logs/.gitkeep
- bybit_bot/tests/test_foundation.py
- bybit_bot/__init__.py (empty)

## 13. Failure / Escalation Conditions
STOP and escalate if:
- Bybit V5 market endpoint schema differs from spec (missing fields)
- OpenRouter free tier models are unavailable or renamed
- Any test fails that cannot be fixed without changing config values
- Scope ambiguity requires touching src/scanner/

## 14. Completion Report Requirements
Status: [COMPLETED/PARTIAL/BLOCKED/FAILED]
Changed Files: [list every file created]
Tests Run: [list tests]
Tests Passed: [count]
Tests Failed: [count + errors]
Known Issues: [any]
Remaining Risks: [any]
Recommended Next Step: TASK-017 Screener + Research Engine

## 15. Review Plan
Sonnet quant review: verify data_aggregator field derivations are correct
Gemini adversarial review: verify error handling / None propagation
CTO final review before TASK-017 begins

## 16. Skill Extraction Decision
NO SKILL — foundation layer, implementation only.
Skill may be created after full system passes paper trading validation.

## 17. Status / Sign-off
Status: PENDING
Approved by: Lead CTO 2026-09-21
Awaiting: Codex implementation + test pass
