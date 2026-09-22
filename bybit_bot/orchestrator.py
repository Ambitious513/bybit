"""Bybit Sniper Bot v2.0 — Master orchestrator / entry point.

Usage:
    python -m bybit_bot.orchestrator --test
    python -m bybit_bot.orchestrator --research
    python -m bybit_bot.orchestrator --plan
    python -m bybit_bot.orchestrator --execute
    python -m bybit_bot.orchestrator --monitor
    python -m bybit_bot.orchestrator --daemon

Startup sequence (--daemon):
    1. Validate required env vars
    2. Test Bybit API (get BTC price)
    3. Test Telegram (send startup message)
    4. Test OpenRouter (non-fatal if fails)
    5. Start APScheduler daemon
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime, time as datetime_time, timedelta

# ── Logging setup ─────────────────────────────────────────────────────────────
os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "logs", "bot.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("orchestrator")

# ── Imports (after logging so errors are captured) ────────────────────────────
from bybit_bot import bybit_api, openrouter, telegram
from bybit_bot.config import (
    BYBIT_API_KEY,
    PAPER_RISK_MARKET,
    SESSION_SKIP_LIST,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_env() -> bool:
    """Check all required env vars are present."""
    required = {
        "BYBIT_API_KEY":      BYBIT_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error("missing_env_vars vars=%s", missing)
        return False
    return True


def _test_bybit() -> bool:
    """Fetch BTC price to confirm Bybit connectivity."""
    ticker = bybit_api.get_ticker("BTCUSDT")
    if ticker is None:
        logger.error("bybit_connectivity_failed")
        return False
    logger.info("bybit_ok btc_price=%s", ticker["price"])
    return True


def _test_telegram(btc_price: str) -> bool:
    """Send startup message to Telegram."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"<b>Bybit Sniper Bot v2.0 Online</b>\n"
        f"<code>{now}</code>\n"
        f"BTC: <b>${float(btc_price):,.2f}</b>\n"
        f"Mode: Paper Trading (GATE-1 approved)\n"
        f"Status: <b>Online</b> ✅"
    )
    ok = telegram.send_message(msg)
    if not ok:
        logger.error("telegram_connectivity_failed")
    return ok


def _test_openrouter() -> bool:
    """Quick OpenRouter ping — non-fatal if fails."""
    result = openrouter.complete("Reply with the single word: OK")
    if result is None:
        logger.warning("openrouter_unavailable — continuing in no-LLM mode")
        return False
    logger.info("openrouter_ok model_response_preview=%s", result[:20])
    return True


# ── CLI commands ──────────────────────────────────────────────────────────────

def cmd_test() -> None:
    """--test: validate connectivity and send startup Telegram message."""
    if not _validate_env():
        sys.exit(1)

    ticker = bybit_api.get_ticker("BTCUSDT")
    if ticker is None:
        telegram.send_message("⚠️ <b>Bybit API unreachable</b> — check connectivity")
        sys.exit(1)

    btc_price = ticker["price"]
    print(f"BTC price: ${float(btc_price):,.2f}")

    ok = _test_telegram(btc_price)
    if not ok:
        logger.error("telegram_test_failed — check token and chat_id")
        sys.exit(1)

    _test_openrouter()
    print("All connectivity checks passed.")


def cmd_research() -> None:
    """--research: run full research pipeline."""
    try:
        from bybit_bot import research
        research.run_research()
    except ImportError:
        logger.warning("research.py not yet implemented (TASK-017)")
        telegram.send_message("⚠️ research.py not yet implemented")
    except Exception as exc:
        logger.exception("research_run_failed error=%s", exc)
        telegram.send_message("⚠️ <b>Research pipeline failed</b> — check bot log")


def cmd_plan() -> None:
    """--plan: run planning from cached research."""
    try:
        from bybit_bot import planning
        planning.run_planning()
    except ImportError:
        logger.warning("planning.py not yet implemented (TASK-018)")
        telegram.send_message("⚠️ planning.py not yet implemented")
    except Exception as exc:
        logger.exception("planning_run_failed error=%s", exc)
        telegram.send_message("⚠️ <b>Planning pipeline failed</b> — check bot log")


def cmd_execute() -> None:
    """--execute: generate manual execution cards from the cached plan inputs."""
    try:
        from bybit_bot import planning
        planning.run_planning()
    except Exception as exc:
        logger.exception("execution_card_run_failed error=%s", exc)
        telegram.send_message("⚠️ <b>Execution-card generation failed</b> — check bot log")


def _bot_data_path(filename: str) -> str:
    """Return a runtime data-file path without exposing project-wide state."""
    return os.path.join(os.path.dirname(__file__), "data", filename)


def load_active_orders() -> list[dict]:
    """Load active order records safely for the BTC regime-flip cancellation alert."""
    try:
        with open(_bot_data_path("active_orders.json"), "r", encoding="utf-8") as handle:
            orders = json.load(handle)
        return orders if isinstance(orders, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("active_orders_load_failed error=%s", exc)
        return []


def _save_active_orders(orders: list[dict]) -> bool:
    """Atomically persist active-order changes made by a Telegram command."""
    path = _bot_data_path("active_orders.json")
    temporary_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(orders, handle, indent=2)
        os.replace(temporary_path, path)
        return True
    except OSError as exc:
        logger.error("active_orders_save_failed error=%s", exc)
        return False


def _normalize_symbol(symbol: str) -> str:
    """Normalize user command symbols to the stored USDT perpetual form."""
    normalized = symbol.upper().strip()
    return normalized if normalized.endswith("USDT") else f"{normalized}USDT"


def _load_balance() -> dict:
    """Load paper-balance state with a safe display-only fallback."""
    default = {"balance": 0.0, "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
    try:
        with open(_bot_data_path("paper_balance.json"), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("paper_balance_display_unavailable error=%s", exc)
        return default


def _load_trade_log() -> list[dict]:
    """Load trade records for optional /balance P&L presentation detail."""
    try:
        with open(_bot_data_path("trade_log.json"), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("trade_log_display_unavailable error=%s", exc)
        return []


def _float(value: object, default: float = 0.0) -> float:
    """Convert command/state numeric input safely to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _approx_trade_breakdown(trades: list[dict]) -> tuple[float, float] | None:
    """Estimate core and runner P&L only when the trade log contains TP inputs."""
    core_total = 0.0
    runner_total = 0.0
    usable = False
    for trade in trades:
        if "core_pnl" in trade and "runner_pnl" in trade:
            core_total += _float(trade["core_pnl"])
            runner_total += _float(trade["runner_pnl"])
            usable = True
            continue
        required = {"entry", "tp1", "tp3", "notional", "side"}
        if not required.issubset(trade):
            continue
        entry = _float(trade["entry"])
        notional = _float(trade["notional"])
        if entry <= 0 or notional <= 0:
            continue
        direction = -1 if trade.get("side") == "SHORT" else 1
        core_total += direction * (_float(trade["tp1"]) - entry) / entry * notional * 0.5
        runner_total += direction * (_float(trade["tp3"]) - entry) / entry * notional * 0.5
        usable = True
    return (round(core_total, 2), round(runner_total, 2)) if usable else None


def handle_filled(symbol: str, price: float, time_str: str) -> None:
    """Mark a matching PENDING manual card FILLED and start its time-stop clock."""
    normalized = _normalize_symbol(symbol)
    try:
        parsed_time = datetime_time.fromisoformat(time_str)
        fill_time = datetime.combine(datetime.now(UTC).date(), parsed_time, tzinfo=UTC).isoformat()
    except ValueError:
        telegram.send_message("⚠️ Invalid fill time — use HH:MM (UTC)")
        return
    orders = load_active_orders()
    for order in orders:
        if order.get("symbol") == normalized and order.get("status") == "PENDING":
            order["status"] = "FILLED"
            order["fill_time_utc"] = fill_time
            if _save_active_orders(orders):
                telegram.send_message(
                    f"✅ Trade logged: <b>{normalized}</b> {order.get('side', 'LONG')} @ ${price:g}\n"
                    "Time-stop clock starts now. 2H limit."
                )
            return
    telegram.send_message(f"No pending order for <b>{normalized}</b>")


def handle_closed(symbol: str, price: float) -> None:
    """Record a user-confirmed manual close and update the paper balance once."""
    normalized = _normalize_symbol(symbol)
    orders = load_active_orders()
    for order in orders:
        if order.get("symbol") == normalized and order.get("status") == "FILLED":
            from bybit_bot import monitor

            pnl = monitor.calculate_pnl(order, price, pct=1.0)
            monitor.log_trade(order, price, "MANUAL_CLOSE")
            monitor.update_paper_balance(pnl)
            order["status"] = "CLOSED"
            if _save_active_orders(orders):
                balance = _load_balance()
                telegram.send_message(f"Trade closed. P&amp;L: {pnl:+.2f} | Balance: ${_float(balance.get('balance')):.2f}")
            return
    telegram.send_message(f"No filled order for <b>{normalized}</b>")


def handle_cancel(symbol: str) -> None:
    """Remove a PENDING manual card from the monitor watchlist."""
    normalized = _normalize_symbol(symbol)
    orders = load_active_orders()
    remaining = [order for order in orders if not (order.get("symbol") == normalized and order.get("status") == "PENDING")]
    if len(remaining) == len(orders):
        telegram.send_message(f"No pending order for <b>{normalized}</b>")
    elif _save_active_orders(remaining):
        telegram.send_message(f"Order cancelled — <b>{normalized}</b> removed from watchlist")


def handle_skip(symbol: str) -> None:
    """Add a symbol to the in-memory session skip list until process restart."""
    normalized = _normalize_symbol(symbol)
    if normalized not in SESSION_SKIP_LIST:
        SESSION_SKIP_LIST.append(normalized)
    telegram.send_message(f"<b>{normalized}</b> skipped this session")


def handle_status() -> None:
    """Send a live summary of all PENDING and FILLED paper orders."""
    now = datetime.now(UTC)
    lines = ["BYBIT SNIPER STATUS"]
    for order in load_active_orders():
        if order.get("status") not in {"PENDING", "FILLED"}:
            continue
        ticker = bybit_api.get_ticker(str(order.get("symbol", "")))
        if not ticker:
            lines.append(f"{order.get('symbol')} — price unavailable")
            continue
        current_price = _float(ticker.get("price"))
        entry = _float(order.get("entry"))
        sl_distance = abs(current_price - _float(order.get("sl"))) / entry * 100 if entry else 0.0
        tp_distance = abs(_float(order.get("tp1")) - current_price) / entry * 100 if entry else 0.0
        hard_close = now.replace(hour=20, minute=0, second=0, microsecond=0)
        remaining = max(timedelta(0), hard_close - now)
        lines.extend([
            f"{order.get('symbol')} {order.get('side')} [{order.get('status')}]",
            f"Entry ${entry:g} | Now ${current_price:g}",
            f"SL {sl_distance:.2f}% | TP1 {tp_distance:.2f}% | Hard close {str(remaining).split('.')[0]}",
        ])
    balance = _load_balance()
    lines.append(f"Paper balance: ${_float(balance.get('balance')):.2f}")
    telegram.send_card(lines)


def handle_btc() -> None:
    """Send a compact BTC capital-flow and deterministic regime summary."""
    from bybit_bot import data_aggregator, quickscan

    ticker = bybit_api.get_ticker("BTCUSDT")
    flow = data_aggregator.get_capital_flow("BTCUSDT") or {}
    regime = quickscan.classify_btc_regime_quick()
    funding = flow.get("fundingRate", {})
    funding_rate = _float(funding.get("latest") if isinstance(funding, dict) else funding)
    telegram.send_card([
        "BTC Quick Check",
        f"Price: ${_float((ticker or {}).get('price')):,.2f}",
        f"Whale: {_float(flow.get('longShortRatio')):.2f}:1",
        f"Fund: {flow.get('fundSide', 'UNKNOWN')}",
        f"Funding: {funding_rate:.6f}",
        f"Regime: {regime}",
    ])


def handle_balance() -> None:
    """Send paper-account statistics, including optional logged core/runner estimates."""
    balance = _load_balance()
    total_trades = int(balance.get("total_trades", 0))
    total_pnl = _float(balance.get("total_pnl"))
    win_rate = int(balance.get("wins", 0)) / total_trades * 100 if total_trades else 0.0
    average_pnl = total_pnl / total_trades if total_trades else 0.0
    message = (
        f"<b>Paper Balance</b>: ${_float(balance.get('balance')):.2f}\n"
        f"Trades: {total_trades} | W:{int(balance.get('wins', 0))} L:{int(balance.get('losses', 0))}\n"
        f"Win Rate: {win_rate:.0f}%\nAvg P&amp;L: ${average_pnl:+.2f}\nTotal P&amp;L: ${total_pnl:+.2f}"
    )
    breakdown = _approx_trade_breakdown(_load_trade_log())
    if breakdown is not None:
        message += f"\nApprox. core at TP1: ${breakdown[0]:+.2f} | runner at TP3: ${breakdown[1]:+.2f}"
    telegram.send_message(message)


def handle_htfltf(symbol: str) -> None:
    """Send the approved HTF/LTF runner-management instruction."""
    telegram.send_message(
        f"⚠️ <b>HTF/LTF CONFLICT {_normalize_symbol(symbol)}</b>\n"
        "→ Close RUNNER (50%) immediately\n→ Keep CORE with SL at breakeven\n→ Reassess next 15M candle"
    )


def handle_market(symbol: str) -> None:
    """Generate a manual weekend market-entry card using the immutable $1 risk override."""
    normalized = _normalize_symbol(symbol)
    try:
        from bybit_bot import data_aggregator, execution, sr_calculator

        ticker = bybit_api.get_ticker(normalized)
        if not ticker:
            telegram.send_message(f"⚠️ Symbol not found: <b>{normalized}</b>")
            return
        current_price = _float(ticker.get("price"))
        sr = sr_calculator.get_sr_levels(normalized, current_price)
        if _float(sr.get("sl_level")) <= 0 or current_price <= 0:
            telegram.send_message(f"⚠️ Stop unavailable — skip <b>{normalized}</b>")
            return
        flow = data_aggregator.get_capital_flow(normalized) or {}
        market_sr = dict(sr)
        market_sr["entry_mid"] = current_price
        market_sr["entry_zone_top"] = current_price
        market_sr["entry_zone_bottom"] = current_price
        market_sr["stop_dist_pct"] = abs(current_price - _float(sr.get("sl_level"))) / current_price * 100
        setup = {"symbol": normalized, "tag": "CRYPTO", "current_price": current_price, "flow": flow, "sr": market_sr, "news": {}, "score": 0}
        original_risk = execution.PAPER_RISK_PER_TRADE
        try:
            execution.PAPER_RISK_PER_TRADE = PAPER_RISK_MARKET
            card = execution.generate_card(setup)
        finally:
            execution.PAPER_RISK_PER_TRADE = original_risk
        if not card:
            telegram.send_message(f"⚠️ Stop geometry invalid — skip <b>{normalized}</b>")
    except Exception as exc:
        logger.exception("market_card_failed symbol=%s error=%s", normalized, exc)
        telegram.send_message(f"⚠️ Market card failed for <b>{normalized}</b>")


def handle_research() -> None:
    """Start full research in a background thread so command polling remains responsive."""
    from bybit_bot import research

    threading.Thread(target=research.run_research, name="research-command", daemon=True).start()
    telegram.send_message("🔍 Research triggered — card incoming in ~30s")


def handle_help() -> None:
    """Send concise descriptions for all supported Telegram commands."""
    telegram.send_message(
        "<b>Commands</b>\n"
        "/filled SYMBOL PRICE HH:MM — log manual fill\n"
        "/closed SYMBOL PRICE — log manual close\n"
        "/cancel SYMBOL — cancel a pending card\n"
        "/skip SYMBOL — skip for this session\n"
        "/status — show active orders\n/btc — BTC flow/regime\n/balance — paper statistics\n"
        "/htfltf SYMBOL — runner management alert\n/market SYMBOL — weekend market card\n"
        "/research — trigger full research\n/help — command list\n/deepdive SYMBOL — on-demand full card"
    )


def _dispatch_telegram_update(update: dict) -> None:
    """Authenticate and dispatch one Telegram text update without raising the poll loop."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict) or str(message.get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
        return
    parts = str(message.get("text", "")).strip().split()
    if not parts:
        return
    command = parts[0].split("@", 1)[0].lower()
    try:
        if command == "/filled" and len(parts) == 4:
            handle_filled(parts[1], float(parts[2]), parts[3])
        elif command == "/closed" and len(parts) == 3:
            handle_closed(parts[1], float(parts[2]))
        elif command == "/cancel" and len(parts) == 2:
            handle_cancel(parts[1])
        elif command == "/skip" and len(parts) == 2:
            handle_skip(parts[1])
        elif command == "/status":
            handle_status()
        elif command == "/btc":
            handle_btc()
        elif command == "/balance":
            handle_balance()
        elif command == "/htfltf" and len(parts) == 2:
            handle_htfltf(parts[1])
        elif command == "/market" and len(parts) == 2:
            handle_market(parts[1])
        elif command == "/research":
            handle_research()
        elif command == "/help":
            handle_help()
        elif command == "/deepdive" and len(parts) == 2:
            handle_deepdive(parts[1])
        elif command == "/deepdive":
            telegram.send_message(
                "⚠️ <b>Missing symbol.</b>\n"
                "Usage: <code>/deepdive SYMBOL</code>\n"
                "Example: <code>/deepdive WIF</code>\n\n"
                "Tap the code block above to copy, then edit and send."
            )
        else:
            telegram.send_message("⚠️ Invalid command. Use /help")
    except (TypeError, ValueError):
        telegram.send_message("⚠️ Invalid command values. Use /help")
    except Exception as exc:
        logger.exception("telegram_command_failed command=%s error=%s", command, exc)
        telegram.send_message("⚠️ Command failed — check bot log")


def _telegram_poll_loop() -> None:
    """Long-poll Telegram updates in a daemon thread beside APScheduler."""
    offset = 0
    while True:
        updates = telegram.get_updates(offset=offset, timeout=30)
        if not updates:
            time.sleep(1)
            continue
        for update in updates:
            update_id = update.get("update_id") if isinstance(update, dict) else None
            _dispatch_telegram_update(update)
            if isinstance(update_id, int):
                offset = max(offset, update_id + 1)


def start_telegram_polling() -> None:
    """Start the authenticated Telegram command poller as a daemon thread."""
    thread = threading.Thread(target=_telegram_poll_loop, name="telegram-command-poller", daemon=True)
    thread.start()
    logger.info("telegram_polling_started")


def load_btc_regime_from_cache() -> str:
    """Return the cached BTC regime for lightweight flip detection."""
    try:
        with open(_bot_data_path("research_cache.json"), "r", encoding="utf-8") as handle:
            research = json.load(handle)
        regime = research.get("regime", {}).get("regime", "CHOPPY")
        return regime if regime in {"BULLISH", "BEARISH", "CHOPPY"} else "CHOPPY"
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        logger.warning("btc_regime_cache_load_failed error=%s", exc)
        return "CHOPPY"


def update_regime_in_cache(current_regime: str) -> None:
    """Persist only the latest lightweight regime classification in research cache."""
    path = _bot_data_path("research_cache.json")
    temporary_path = f"{path}.tmp"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            research = json.load(handle)
        if not isinstance(research, dict):
            raise ValueError("invalid research cache")
        regime = research.setdefault("regime", {})
        if not isinstance(regime, dict):
            raise ValueError("invalid cached regime")
        regime["regime"] = current_regime
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(research, handle, indent=2)
        os.replace(temporary_path, path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("btc_regime_cache_update_failed error=%s", exc)


def handle_deepdive(symbol_input: str) -> None:
    """Build one manual execution card for an on-demand symbol research request."""
    symbol = symbol_input.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        from bybit_bot import data_aggregator, execution, openrouter, planning, sr_calculator

        ticker = bybit_api.get_ticker(symbol)
        if not ticker:
            telegram.send_message(f"⚠️ Symbol not found: <b>{symbol}</b>")
            return
        flow = data_aggregator.get_capital_flow(symbol)
        if not flow:
            telegram.send_message(f"⚠️ Flow unavailable: <b>{symbol}</b>")
            return
        price = float(ticker["price"])
        sr = sr_calculator.get_sr_levels(symbol, price)
        prompt = f"Coin: {symbol}. Return its current news sentiment and any unlock or exploit risk as JSON."
        response = openrouter.complete(prompt, system_prompt=(
            'Return JSON only: {"sentiment":"BULLISH|BEARISH|NEUTRAL","risk_events":[],'
            '"unlock_today":false,"exploit_today":false}'
        ))
        news = openrouter.parse_json_response(response) or {
            "sentiment": "NEUTRAL", "risk_events": [], "unlock_today": False, "exploit_today": False,
        }
        score = planning.score_coin(symbol, flow, news, "CRYPTO")
        if score < 0:
            telegram.send_message(f"⚠️ <b>{symbol}</b> score {score} — below threshold")
            return
        setup = {"symbol": symbol, "score": score, "tag": "CRYPTO", "current_price": price, "flow": flow, "sr": sr, "news": news}
        card = execution.generate_card(setup)
        if not card:
            telegram.send_message(f"⚠️ Stop too wide — skip <b>{symbol}</b>")
        logger.info("deepdive_completed symbol=%s score=%s", symbol, score)
    except Exception as exc:
        logger.exception("deepdive_failed symbol=%s error=%s", symbol, exc)
        telegram.send_message(f"⚠️ Deep dive failed for <b>{symbol}</b> — check bot log")


def cmd_monitor() -> None:
    """--monitor: run one monitoring cycle."""
    try:
        from bybit_bot import monitor
        monitor.run_monitor_cycle()
    except ImportError:
        logger.warning("monitor.py not yet implemented (TASK-020)")
    except Exception as exc:
        logger.exception("monitor_cycle_failed error=%s", exc)
        telegram.send_message("⚠️ <b>Monitor cycle failed</b> — check bot log")


def cmd_daemon() -> None:
    """--daemon: start APScheduler with all 4 jobs."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        logger.error("apscheduler not installed — run: pip install APScheduler==3.10.4")
        sys.exit(1)

    if not _validate_env():
        sys.exit(1)

    # Startup checks
    ticker = bybit_api.get_ticker("BTCUSDT")
    if ticker is None:
        msg = "⚠️ <b>Bybit API unavailable at startup</b> — bot not started"
        telegram.send_message(msg)
        logger.error("startup_bybit_failed — exiting")
        sys.exit(1)

    if not _test_telegram(ticker["price"]):
        logger.error("startup_telegram_failed — exiting")
        sys.exit(1)

    _test_openrouter()  # non-fatal
    start_telegram_polling()

    scheduler = BlockingScheduler(timezone="UTC")

    # Research: every 4 hours
    scheduler.add_job(
        cmd_research, "interval", hours=4,
        id="research", name="Full research pipeline",
        max_instances=1,
    )

    # Monitor: every 2 minutes
    scheduler.add_job(
        cmd_monitor, "interval", minutes=2,
        id="monitor", name="Position monitor",
        max_instances=1,
    )

    # BTC check: every 15 minutes
    scheduler.add_job(
        _btc_check, "interval", minutes=15,
        id="btc_check", name="BTC regime check",
        max_instances=1,
    )

    # Quick scan: every 30 minutes; skips if full research ran in last 20M.
    scheduler.add_job(
        _quickscan_job, "interval", minutes=30,
        id="quickscan", name="Quick scan",
        max_instances=1,
    )

    logger.info("scheduler_started jobs=%d", len(scheduler.get_jobs()))

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("scheduler_stopped — keyboard interrupt")
        scheduler.shutdown()


def _btc_check() -> None:
    """Every-15-minute BTC regime check."""
    try:
        from bybit_bot import quickscan
        current_regime = quickscan.classify_btc_regime_quick()
        previous_regime = load_btc_regime_from_cache()
        if previous_regime == "BULLISH" and current_regime in {"BEARISH", "CHOPPY"}:
            pending_symbols = [
                str(order.get("symbol")) for order in load_active_orders()
                if isinstance(order, dict) and order.get("status") == "PENDING"
            ]
            if pending_symbols:
                telegram.send_message(
                    f"⚠️ <b>BTC REGIME FLIP → {current_regime}</b>\n"
                    "→ Cancel all pending limit orders NOW\n"
                    f"→ Pending: {', '.join(pending_symbols)}"
                )
        update_regime_in_cache(current_regime)
    except ImportError:
        logger.warning("quickscan.py not yet implemented (TASK-021)")
    except Exception as exc:
        logger.exception("btc_check_failed error=%s", exc)


def _quickscan_job() -> None:
    """Every-30-minute quick scan."""
    try:
        from bybit_bot import quickscan
        quickscan.run_quickscan()
    except ImportError:
        logger.warning("quickscan.py not yet implemented (TASK-021)")
    except Exception as exc:
        logger.exception("quickscan_job_failed error=%s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit Sniper Bot v2.0")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test",     action="store_true", help="Test all connections")
    group.add_argument("--research", action="store_true", help="Run research pipeline")
    group.add_argument("--plan",     action="store_true", help="Run planning from cache")
    group.add_argument("--execute",  action="store_true", help="Generate manual execution cards")
    group.add_argument("--deepdive", metavar="SYMBOL", help="Run on-demand full research for one symbol")
    group.add_argument("--monitor",  action="store_true", help="Run one monitor cycle")
    group.add_argument("--daemon",   action="store_true", help="Start scheduled daemon")
    args = parser.parse_args()

    if args.test:     cmd_test()
    elif args.research: cmd_research()
    elif args.plan:   cmd_plan()
    elif args.execute: cmd_execute()
    elif args.deepdive: handle_deepdive(args.deepdive)
    elif args.monitor: cmd_monitor()
    elif args.daemon: cmd_daemon()


if __name__ == "__main__":
    main()
