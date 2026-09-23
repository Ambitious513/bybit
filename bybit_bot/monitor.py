"""Read-only paper-position monitor for manually confirmed FILLED orders."""

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_bot import bybit_api, sr_calculator, telegram
from bybit_bot.config import (
    CROWDED_FUNDING_THRESHOLD,
    HARD_CLOSE_UTC_HOUR,
    PAPER_BALANCE,
    PAPER_BALANCE_FLOOR,
    TIME_STOP_HOURS,
)

logger = logging.getLogger("monitor")

_ORDERS_LOCK = threading.Lock()

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_ACTIVE_ORDERS_PATH = os.path.join(_DATA_DIR, "active_orders.json")
_TRADE_LOG_PATH = os.path.join(_DATA_DIR, "trade_log.json")
_PAPER_BALANCE_PATH = os.path.join(_DATA_DIR, "paper_balance.json")
_RESEARCH_CACHE_PATH = os.path.join(_DATA_DIR, "research_cache.json")


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time for deterministic comparisons."""
    return datetime.now(UTC)


def _float(value: Any, default: float = 0.0) -> float:
    """Convert a data field to float without allowing malformed data to crash monitoring."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp as UTC, returning None and logging invalid values."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        logger.error("timestamp_parse_error value=%s error=%s", value, exc)
        return None


def _load_json_array(path: str, description: str) -> list[dict] | None:
    """Load a JSON array, returning None for a corrupt file so it is not overwritten."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("expected JSON array")
        return payload
    except FileNotFoundError:
        return []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("%s_load_error error=%s", description, exc)
        return None


def _atomic_write(path: str, payload: Any, description: str) -> bool:
    """Write JSON atomically and return whether persistence completed successfully."""
    temporary_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(temporary_path, path)
        return True
    except OSError as exc:
        logger.error("%s_save_error error=%s", description, exc)
        return False


def calculate_pnl(order: dict, exit_price: float, pct: float = 1.0) -> float:
    """Calculate rounded paper P&L for a LONG or SHORT fraction of an order."""
    entry = _float(order.get("fill_price") or order.get("entry"))
    notional = _float(order.get("notional"))
    if entry <= 0:
        logger.error("pnl_invalid_entry symbol=%s", order.get("symbol"))
        return 0.0
    if order.get("side") == "SHORT":
        pnl = (entry - exit_price) / entry * notional * pct
    else:
        pnl = (exit_price - entry) / entry * notional * pct
    return round(pnl, 2)


def log_trade(order: dict, exit_price: float, exit_reason: str) -> None:
    """Append a closed paper trade to the append-only trade-log JSON array."""
    trades = _load_json_array(_TRADE_LOG_PATH, "trade_log")
    if trades is None:
        telegram.send_message("⚠️ <b>Trade log corrupted</b> — monitor skipped log write")
        return
    record = {
        "symbol": order.get("symbol"), "side": order.get("side"), "entry": _float(order.get("entry")),
        "planned_entry": _float(order.get("planned_entry", order.get("entry"))),
        "fill_price": _float(order.get("fill_price", order.get("entry"))),
        "sl": _float(order.get("sl")),
        "tp1": _float(order.get("tp1")),
        "tp3": _float(order.get("tp3")),
        "notional": _float(order.get("notional")),
        "leverage": order.get("leverage"),
        "stop_dist_pct": _float(order.get("stop_dist_pct")),
        "exit_price": float(exit_price), "exit_reason": exit_reason,
        "pnl": calculate_pnl(order, exit_price), "paper_risk": _float(order.get("paper_risk")),
        "score": order.get("score", 0), "opened_at": order.get("fill_time_utc"),
        "closed_at": _utc_now().isoformat(),
    }
    _atomic_write(_TRADE_LOG_PATH, [*trades, record], "trade_log")


def update_paper_balance(pnl: float) -> None:
    """Update paper account statistics and warn if its immutable floor is breached."""
    default = {"balance": PAPER_BALANCE, "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
    try:
        with open(_PAPER_BALANCE_PATH, "r", encoding="utf-8") as handle:
            balance_data = json.load(handle)
        if not isinstance(balance_data, dict):
            raise ValueError("expected JSON object")
    except FileNotFoundError:
        balance_data = default
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("paper_balance_load_error error=%s", exc)
        telegram.send_message("⚠️ <b>Paper-balance state corrupted</b> — monitor skipped balance write")
        return
    balance_data["balance"] = round(_float(balance_data.get("balance"), PAPER_BALANCE) + pnl, 2)
    balance_data["total_trades"] = int(balance_data.get("total_trades", 0)) + 1
    balance_data["wins"] = int(balance_data.get("wins", 0)) + (1 if pnl > 0 else 0)
    balance_data["losses"] = int(balance_data.get("losses", 0)) + (0 if pnl > 0 else 1)
    balance_data["total_pnl"] = round(_float(balance_data.get("total_pnl")) + pnl, 2)
    if not _atomic_write(_PAPER_BALANCE_PATH, balance_data, "paper_balance"):
        return
    if balance_data["balance"] < PAPER_BALANCE_FLOOR:
        telegram.send_message(
            f"⚠️ <b>BALANCE WARNING</b>: ${balance_data['balance']:.2f} below floor ${PAPER_BALANCE_FLOOR:.2f}\n"
            "→ Review performance before next trade"
        )


def load_btc_regime_from_cache() -> str:
    """Load a fresh BTC regime from cache, defaulting safely to CHOPPY after five hours."""
    try:
        with open(_RESEARCH_CACHE_PATH, "r", encoding="utf-8") as handle:
            research = json.load(handle)
        timestamp = _parse_iso(research.get("timestamp"))
        regime_data = research.get("regime") if isinstance(research, dict) else None
        if timestamp is None or _utc_now() - timestamp > timedelta(hours=5):
            logger.warning("btc_regime_cache_stale")
            return "CHOPPY"
        if not isinstance(regime_data, dict):
            return "CHOPPY"
        # A legacy cache can omit btc_support; monitor does not use a 0.0
        # fallback as a real price level.
        _float(regime_data.get("btc_support"), 0.0)
        regime = str(regime_data.get("regime", "CHOPPY"))
        return regime if regime in {"BULLISH", "BEARISH", "CHOPPY"} else "CHOPPY"
    except (OSError, ValueError, json.JSONDecodeError, AttributeError) as exc:
        logger.warning("btc_regime_cache_unavailable error=%s", exc)
        return "CHOPPY"


def check_funding_creep(order: dict, current_funding: float) -> str | None:
    """Return a funding-creep warning message if the order has crossed the crowded threshold.

    Fires AT MOST ONCE per order — guarded by the ``funding_warned`` flag.
    Returns the alert text when the alert should fire, None otherwise.
    Caller must set ``order["funding_warned"] = True`` and persist before sending.
    """
    if order.get("funding_warned"):
        return None
    if abs(current_funding) >= CROWDED_FUNDING_THRESHOLD:
        symbol = order.get("symbol", "UNKNOWN")
        return (
            f"⚠️ FUNDING CREEP — {symbol}\n"
            f"   Funding now: {current_funding * 100:.4f}%\n"
            f"   Threshold:   {CROWDED_FUNDING_THRESHOLD * 100:.4f}%\n"
            "→ Position is now in overcrowded territory\n"
            "→ Consider closing RUNNER early\n"
            "→ Keep CORE — do NOT override TP1"
        )
    return None


def _close_order(order: dict, exit_price: float, reason: str) -> None:
    """Record and account for an order that the deterministic monitor closes."""
    order["status"] = "CLOSED"
    log_trade(order, exit_price, reason)
    update_paper_balance(calculate_pnl(order, exit_price))


def check_order(order: dict, current_price: float, btc_price: float, btc_regime: str) -> tuple[list[str], dict]:
    """Apply core checks 0–7 to one order and return alerts plus its updated state."""
    updated = dict(order)
    alerts: list[str] = []
    now = _utc_now()
    symbol = str(updated.get("symbol", ""))
    status = updated.get("status")

    if status == "PENDING":
        expiry = _parse_iso(updated.get("expiry_utc"))
        if expiry is None:
            logger.warning("pending_order_expiry_invalid symbol=%s", symbol)
        elif now > expiry:
            alerts.append(f"⏰ <b>ORDER EXPIRED {symbol}</b>\n→ Cancel limit order on Bybit now")
            updated["status"] = "EXPIRED"
        return alerts, updated
    if status != "FILLED":
        return alerts, updated

    side = updated.get("side")
    sl_hit = (side == "LONG" and current_price <= _float(updated.get("sl"))) or (
        side == "SHORT" and current_price >= _float(updated.get("sl"))
    )
    if sl_hit:
        pnl = calculate_pnl(updated, current_price)
        alerts.append(
            f"🔴 <b>SL HIT {symbol}</b> at ${current_price:g}\n→ CLOSE ALL NOW\n→ Paper P&amp;L: {pnl:+.2f}"
        )
        _close_order(updated, current_price, "SL")
        return alerts, updated

    tp1_hit = (side == "LONG" and current_price >= _float(updated.get("tp1"))) or (
        side == "SHORT" and current_price <= _float(updated.get("tp1"))
    )
    if not updated.get("core_closed", False) and tp1_hit:
        core_pnl = calculate_pnl(updated, _float(updated.get("tp1")), pct=0.50)
        alerts.append(
            f"✅ <b>TP1 HIT {symbol}</b> at ${_float(updated.get('tp1')):g}\n→ CLOSE CORE (50%) NOW\n"
            f"→ Move SL to entry ${_float(updated.get('entry')):g} (breakeven)\n"
            f"→ Paper core P&amp;L: +${core_pnl:.2f}\n→ Runner targeting TP2 ${_float(updated.get('tp2')):g}"
        )
        updated["core_closed"] = True
        updated["sl"] = updated.get("entry")

    tp2_hit = (side == "LONG" and current_price >= _float(updated.get("tp2"))) or (
        side == "SHORT" and current_price <= _float(updated.get("tp2"))
    )
    if updated.get("core_closed", False) and not updated.get("runner_closed", False) and tp2_hit and not updated.get("tp2_alerted", False):
        alerts.append(
            f"🎯 <b>TP2 HIT {symbol}</b> at ${_float(updated.get('tp2')):g}\n→ CLOSE 60% OF RUNNER NOW\n"
            f"→ Let remaining 40% ride to TP3 ${_float(updated.get('tp3')):g}"
        )
        updated["tp2_alerted"] = True

    tp3_hit = (side == "LONG" and current_price >= _float(updated.get("tp3"))) or (
        side == "SHORT" and current_price <= _float(updated.get("tp3"))
    )
    if updated.get("core_closed", False) and not updated.get("runner_closed", False) and tp3_hit:
        full_pnl = calculate_pnl(updated, current_price)
        alerts.append(
            f"🏆 <b>TP3 HIT {symbol}</b> — FULL CLOSE\n→ Close remaining runner\n→ Full paper P&amp;L: +${full_pnl:.2f}"
        )
        updated["runner_closed"] = True
        _close_order(updated, current_price, "TP3")
        return alerts, updated

    fill_time = _parse_iso(updated.get("fill_time_utc"))
    if not updated.get("core_closed", False) and fill_time and now - fill_time >= timedelta(hours=TIME_STOP_HOURS):
        alerts.append(f"⏰ <b>TIME-STOP {symbol}</b>\n→ 2 hours since fill, no TP1 hit\n→ CLOSE ALL NOW at ${current_price:g}")
    if now.hour >= HARD_CLOSE_UTC_HOUR:
        alerts.append(f"🔴 <b>HARD CLOSE — {HARD_CLOSE_UTC_HOUR}:00 UTC</b>\n→ CLOSE ALL POSITIONS NOW")
    if btc_regime != "BULLISH" and side == "LONG":
        alerts.append(f"⚠️ <b>BTC REGIME FLIP → {btc_regime}</b>\n→ CLOSE ALL LONGS NOW\n→ {symbol} at ${current_price:g}")
    alerts.extend(_thin_volume_alerts(updated, current_price, now))
    return alerts, updated


def _pending_advanced_alerts(order: dict, now: datetime) -> list[str]:
    """Run weekend-escalation and in-zone dead-cat checks for a PENDING order."""
    symbol = str(order.get("symbol", ""))
    alerts: list[str] = []
    ticker = bybit_api.get_ticker(symbol)
    if not ticker:
        logger.warning("pending_monitor_price_unavailable symbol=%s", symbol)
        return alerts
    current_price = _float(ticker.get("price"))
    if now.weekday() in {5, 6}:
        candles = sr_calculator._api_candles(bybit_api.get_klines(symbol, "5", 3))
        last_two = candles[-2:]
        if len(last_two) == 2 and all(candle["close"] > _float(order.get("zone_top")) for candle in last_two):
            alerts.append(
                f"⚡ <b>WEEKEND ESCALATION {symbol}</b>\n"
                "Price holding above zone 2+ candles\n"
                f"→ Consider MARKET entry at ${current_price:g}\n"
                "→ Adjust SL to nearest 5M support\n"
                f"→ Reply /market {symbol.removesuffix('USDT')} to confirm"
            )
    if _float(order.get("zone_bottom")) <= current_price <= _float(order.get("zone_top")):
        result = sr_calculator.dead_cat_check(symbol, _float(order.get("zone_bottom")))
        if result.get("passed"):
            alerts.append(
                f"✅ <b>ENTRY CONFIRMED {symbol}</b>\n"
                "Dead cat filter PASSED — all 3 checks green\n"
                "→ Safe to place limit order now"
            )
        else:
            # Build human-readable list of which checks failed.
            # New return format uses flat booleans (Phase 1 + Phase 2).
            _PHASE_LABELS = {
                "bounce_above_midpoint": "midpoint close",
                "bounce_volume_ok":      "bounce volume",
                "higher_low":            "higher low",
            }
            failed = [label for key, label in _PHASE_LABELS.items()
                      if not result.get(key, True)]
            if result.get("drop_was_strong"):
                failed.insert(0, "high-vol drop (Phase 1)")
            alerts.append(
                f"⚠️ <b>DEAD CAT WARNING {symbol}</b>\n"
                f"Failed checks: {', '.join(failed) or 'unknown'}\n"
                "→ Wait for next 5M candle"
            )
    return alerts


def _thin_volume_alerts(order: dict, current_price: float, now: datetime) -> list[str]:
    """Warn only for TradFi positions close to SL in the approved low-volume window."""
    if order.get("tag") != "TRADFI" or now.weekday() >= 5 or not 18 <= now.hour < 20:
        return []
    entry = _float(order.get("entry"))
    sl = _float(order.get("sl"))
    if entry <= 0:
        return []
    distance_to_sl_pct = abs(current_price - sl) / entry * 100
    if distance_to_sl_pct >= 0.3:
        return []
    return [
        f"⚠️ <b>THIN LIQUIDITY WARNING {order.get('symbol')}</b>\n"
        f"Price ${current_price:g} approaching SL ${sl:g}\n"
        "Low volume window (18-20 UTC)\n"
        "→ Green 5M close above midpoint = HOLD\n"
        "→ Red close below midpoint = EXIT manually"
    ]


def run_monitor_cycle() -> None:
    """Run one 2-minute read-only monitoring cycle over persisted paper orders."""
    if not _ORDERS_LOCK.acquire(timeout=10.0):
        logger.error("monitor_lock_timeout — skipping cycle")
        return
    try:
        orders = _load_json_array(_ACTIVE_ORDERS_PATH, "active_orders")
        if orders is None:
            telegram.send_message("⚠️ <b>Active-order state corrupted</b> — monitor skipped cycle")
            return
        if not orders:
            logger.info("no_open_orders")
            return
        btc_regime = load_btc_regime_from_cache()
        updated_orders: list[dict] = []
        for order in orders:
            if not isinstance(order, dict):
                logger.warning("invalid_order_record_skipped")
                updated_orders.append(order)
                continue
            if order.get("status") == "PENDING":
                alerts, updated = check_order(order, 0.0, 0.0, btc_regime)
                if updated.get("status") == "PENDING":
                    alerts.extend(_pending_advanced_alerts(updated, _utc_now()))
            elif order.get("status") == "FILLED":
                symbol = str(order.get("symbol", ""))
                ticker = bybit_api.get_ticker(symbol)
                btc_ticker = bybit_api.get_ticker("BTCUSDT")
                if not ticker or not btc_ticker:
                    logger.error("monitor_price_unavailable symbol=%s", symbol)
                    telegram.send_message(f"⚠️ <b>Monitor price unavailable</b> — {symbol}; retrying next cycle")
                    updated_orders.append(order)
                    continue
                current_price = _float(ticker.get("price"))
                alerts, updated = check_order(order, current_price, _float(btc_ticker.get("price")), btc_regime)
                # R6 — Funding creep check (once per order)
                funding_data = bybit_api.get_funding_rate(symbol)
                if funding_data is not None:
                    current_funding = _float(funding_data.get("fundingRate"))
                    creep_warning = check_funding_creep(updated, current_funding)
                    if creep_warning is not None:
                        updated["funding_warned"] = True
                        # Persist the flag before sending the alert to guarantee
                        # at-most-once delivery even if the process restarts.
                        _atomic_write(_ACTIVE_ORDERS_PATH, [*updated_orders, updated], "active_orders")
                        telegram.send_message(creep_warning)
            else:
                alerts, updated = [], order
            for alert in alerts:
                telegram.send_message(alert)
            updated_orders.append(updated)
        _atomic_write(_ACTIVE_ORDERS_PATH, updated_orders, "active_orders")
    finally:
        _ORDERS_LOCK.release()
