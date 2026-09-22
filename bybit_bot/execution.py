"""Paper-trading execution-card generator.

This module produces manual-action Telegram alerts and persists PENDING card
state. It contains no Bybit trading or order-placement functionality.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_bot import telegram
from bybit_bot.config import (
    BTC_INVALIDATION,
    CORE_PCT,
    HARD_CLOSE_UTC_HOUR,
    PAPER_RISK_PER_TRADE,
    RUNNER_PCT,
    WINDOW_PATIENT_MINS,
    WINDOW_SET_FORGET_MINS,
    WINDOW_URGENT_MINS,
)

logger = logging.getLogger("execution")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_ACTIVE_ORDERS_PATH = os.path.join(_DATA_DIR, "active_orders.json")
_RESEARCH_CACHE_PATH = os.path.join(_DATA_DIR, "research_cache.json")


def _float(value: Any, default: float = 0.0) -> float:
    """Convert a numeric data field to float without raising to callers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_regime_context() -> dict | None:
    """Load required BTC price and direction context from research cache."""
    try:
        with open(_RESEARCH_CACHE_PATH, "r", encoding="utf-8") as handle:
            research = json.load(handle)
        regime = research.get("regime") if isinstance(research, dict) else None
        if not isinstance(regime, dict) or regime.get("direction") not in {"LONG", "SHORT"}:
            logger.error("execution_regime_direction_unavailable")
            return None
        return regime
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("execution_research_cache_unavailable error=%s", exc)
        return None


def _price(value: float) -> str:
    """Format a card price at the precision required by the specification."""
    if value > 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def _load_orders() -> list[dict]:
    """Load existing pending/filled paper orders, treating absent data as empty."""
    try:
        with open(_ACTIVE_ORDERS_PATH, "r", encoding="utf-8") as handle:
            orders = json.load(handle)
        return orders if isinstance(orders, list) else []
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("active_orders_load_error error=%s", exc)
        return []


def save_pending_order(card: dict) -> None:
    """Append a PENDING card to active_orders.json using an atomic replacement."""
    orders = _load_orders()
    orders.append(card)
    temporary_path = f"{_ACTIVE_ORDERS_PATH}.tmp"
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(orders, handle, indent=2)
        os.replace(temporary_path, _ACTIVE_ORDERS_PATH)
        logger.info("order_saved symbol=%s expiry=%s", card["symbol"], card["expiry_utc"])
    except OSError as exc:
        logger.error("active_order_save_error symbol=%s error=%s", card.get("symbol"), exc)


def send_execution_card_telegram(card: dict) -> bool:
    """Send the exact monospace execution-card layout to Telegram."""
    issued = datetime.now(UTC).strftime("%H:%M")
    expiry = datetime.fromisoformat(card["expiry_utc"]).astimezone(UTC).strftime("%H:%M")
    btc_price = _float(card.get("btc_price"))
    core_profit = card["risk"] * CORE_PCT * (0.8 if card["tag"] == "TRADFI" else 1.0)
    full_profit = core_profit + card["risk"] * RUNNER_PCT * 2.5
    label = card["window_label"].replace("SET & FORGET", "S&F")
    lines = [
        "╔══════════════════════════════════════════╗",
        f"║  ⚡ EXECUTION CARD — {card['symbol']} {card['side']}",
        f"║  Whale {card['whale_ratio']:.1f}:1 | Fund {card['fund_side']}",
        "╠══════════════════════════════════════════╣",
        f"║ BTC: ${_price(btc_price)} {card['regime']}",
        f"║ ⏰ HARD CLOSE: {HARD_CLOSE_UTC_HOUR:02d}:00 UTC",
        "╠══════════════════════════════════════════╣",
        f"║ ⏱️ VALIDITY: {issued} → {expiry} UTC",
        f"║ GAP: {card['gap_pct']:.2f}% — {label}",
        "╠══════════════════════════════════════════╣",
        "║ ✅ PRE-ENTRY CHECKLIST",
        f"║  □ 15M candle CLOSED ≥ ${_price(card['zone_bottom'])}",
        "║  □ 5M candle GREEN with volume",
        "║  □ Candle closes ABOVE its midpoint",
        "║  □ Volume ≥ 70% of prior 3 candles",
        f"║  □ BTC holding above ${_price(_float(card.get('btc_support')))}",
        "║  □ No negative news last 5 mins",
        "║  ❌ Any box fails → wait next candle",
        "╠══════════════════════════════════════════╣",
        f"║ ENTRY: LIMIT ${_price(card['entry'])}",
        f"║ ZONE:  ${_price(card['zone_bottom'])} – ${_price(card['zone_top'])}",
        "╠══════════════════════════════════════════╣",
        f"║ STOP LOSS: ${_price(card['sl'])} (−{card['stop_dist_pct']:.2f}% from entry)",
        f"║ LEVERAGE:  {card['leverage']}x",
        f"║ ☠️ BTC loses ${BTC_INVALIDATION:,.2f} → exit",
        f"║ ☠️ {expiry} UTC passes unfilled→ cancel",
        "║ ☠️ No TP1 in 2h → close all",
        "╠══════════════════════════════════════════╣",
        "║ TWO-LAYER TAKE PROFIT",
        "║ CORE (50%) — NO EXCEPTIONS:",
        f"║  TP1: ${_price(card['tp1'])}  [+{(abs(card['tp1'] - card['entry']) / card['entry'] * 100):.2f}%]  ~${core_profit:.2f}",
        "║ RUNNER (50%) — after TP1:",
        f"║  TP2: ${_price(card['tp2'])}  [+{(abs(card['tp2'] - card['entry']) / card['entry'] * 100):.2f}%]  ~${card['risk'] * RUNNER_PCT * 1.5:.2f}",
        f"║  TP3: ${_price(card['tp3'])}  [+{(abs(card['tp3'] - card['entry']) / card['entry'] * 100):.2f}%]  ~${card['risk'] * RUNNER_PCT * 2.5:.2f}",
        f"║  Core guaranteed: ~${core_profit:.2f} paper",
        f"║  Full target:     ~${full_profit:.2f} paper",
        "╠══════════════════════════════════════════╣",
        f"║ SIZING: Risk ${card['risk']:.2f} | {card['leverage']}x | Qty ~{card['qty']:.6g}",
        "╠══════════════════════════════════════════╣",
        f"║ POST-TP1: Close CORE → SL to ${_price(card['entry'])}",
        "║ HTF/LTF diverge → close RUNNER only",
        "╚══════════════════════════════════════════╝",
    ]
    boxed_lines = [
        f"{line}{' ' * max(0, 43 - len(line))}║" if line.startswith("║") else line
        for line in lines
    ]
    return telegram.send_card(boxed_lines)


def generate_card(setup: dict) -> dict | None:
    """Generate, notify, and persist a manual PENDING paper-trading card.

    The function never contacts an order endpoint. Invalid stop geometry or
    unavailable regime context prevents card creation rather than guessing.
    """
    context = _load_regime_context()
    if context is None:
        return None
    sr = setup.get("sr") if isinstance(setup.get("sr"), dict) else {}
    flow = setup.get("flow") if isinstance(setup.get("flow"), dict) else {}
    current_price = _float(setup.get("current_price"))
    entry = _float(sr.get("entry_mid"))
    sl = _float(sr.get("sl_level"))
    stop_dist_pct = _float(sr.get("stop_dist_pct"))
    side = context["direction"]
    if current_price <= 0 or entry <= 0 or stop_dist_pct <= 0:
        logger.error("execution_invalid_setup symbol=%s", setup.get("symbol"))
        return None
    if stop_dist_pct >= 8.0:
        logger.warning("stop_too_wide symbol=%s stop_dist_pct=%s", setup.get("symbol"), stop_dist_pct)
        return None
    if (side == "LONG" and sl >= entry) or (side == "SHORT" and sl <= entry):
        logger.error("execution_invalid_stop_geometry symbol=%s side=%s", setup.get("symbol"), side)
        return None

    gap_pct = max(0.0, (current_price - _float(sr.get("entry_zone_top"))) / current_price * 100)
    if gap_pct < 1.0:
        window_mins, window_label = WINDOW_URGENT_MINS, "URGENT"
    elif gap_pct < 3.0:
        window_mins, window_label = WINDOW_PATIENT_MINS, "PATIENT"
    else:
        window_mins, window_label = WINDOW_SET_FORGET_MINS, "SET & FORGET"
    leverage = 10 if stop_dist_pct < 1.0 else 5 if stop_dist_pct < 5.0 else 3
    tag = str(setup.get("tag", ""))
    multiplier = 0.8 if tag == "TRADFI" else 1.0
    direction = 1 if side == "LONG" else -1
    stop_fraction = stop_dist_pct / 100
    tp1 = entry + direction * entry * stop_fraction * multiplier
    tp2 = entry + direction * entry * stop_fraction * 1.5
    tp3 = entry + direction * entry * stop_fraction * 2.5
    risk = PAPER_RISK_PER_TRADE
    notional = risk / stop_fraction
    now = datetime.now(UTC)
    card = {
        "symbol": str(setup.get("symbol", "")), "side": side, "entry": entry,
        "zone_top": _float(sr.get("entry_zone_top")), "zone_bottom": _float(sr.get("entry_zone_bottom")),
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "leverage": leverage,
        "stop_dist_pct": stop_dist_pct, "gap_pct": gap_pct, "window_mins": window_mins,
        "window_label": window_label, "expiry_utc": (now + timedelta(minutes=window_mins)).isoformat(),
        "notional": notional, "margin": notional / leverage, "qty": notional / entry,
        "risk": risk, "tag": tag, "core_closed": False, "runner_closed": False,
        "fill_time_utc": None, "paper_risk": risk, "status": "PENDING",
        "whale_ratio": _float(flow.get("longShortRatio")), "fund_side": str(flow.get("fundSide", "UNKNOWN")),
        "score": int(setup.get("score", 0)), "btc_price": _float(context.get("btc_price")),
        "regime": str(context.get("regime", "UNKNOWN")), "btc_support": _float(context.get("btc_support")),
    }
    send_execution_card_telegram(card)
    save_pending_order(card)
    return card
