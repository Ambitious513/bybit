"""Bybit Sniper Bot v2.0 — Master orchestrator / entry point.

Usage:
    python -m bybit_bot.orchestrator --test
    python -m bybit_bot.orchestrator --research
    python -m bybit_bot.orchestrator --plan
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
import logging
import os
import sys
from datetime import UTC, datetime

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
from bybit_bot.config import BYBIT_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


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


def cmd_plan() -> None:
    """--plan: run planning from cached research."""
    try:
        from bybit_bot import planning
        planning.run_planning()
    except ImportError:
        logger.warning("planning.py not yet implemented (TASK-018)")
        telegram.send_message("⚠️ planning.py not yet implemented")


def cmd_monitor() -> None:
    """--monitor: run one monitoring cycle."""
    try:
        from bybit_bot import monitor
        monitor.run_monitor_cycle()
    except ImportError:
        logger.warning("monitor.py not yet implemented (TASK-020)")


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

    # Quick scan: every 30 minutes
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
        quickscan.classify_btc_regime_quick()
    except ImportError:
        pass


def _quickscan_job() -> None:
    """Every-30-minute quick scan."""
    try:
        from bybit_bot import quickscan
        quickscan.run_quickscan()
    except ImportError:
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit Sniper Bot v2.0")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test",     action="store_true", help="Test all connections")
    group.add_argument("--research", action="store_true", help="Run research pipeline")
    group.add_argument("--plan",     action="store_true", help="Run planning from cache")
    group.add_argument("--monitor",  action="store_true", help="Run one monitor cycle")
    group.add_argument("--daemon",   action="store_true", help="Start scheduled daemon")
    args = parser.parse_args()

    if args.test:     cmd_test()
    elif args.research: cmd_research()
    elif args.plan:   cmd_plan()
    elif args.monitor: cmd_monitor()
    elif args.daemon: cmd_daemon()


if __name__ == "__main__":
    main()
