"""Bybit Sniper Bot v2.0 — Configuration
All values loaded from .env. Never hardcode credentials.
Source of truth: docs/STRATEGY_SPEC.md + docs/RISK_SPEC.md (GATE-1 locked).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API CREDENTIALS ──────────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_BASE_URL   = "https://api.bybit.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── OPENROUTER KEY ROTATION ───────────────────────────────────────────────────
OPENROUTER_KEYS = [
    os.getenv("OPENROUTER_KEY_1"),
    os.getenv("OPENROUTER_KEY_2"),
    os.getenv("OPENROUTER_KEY_3"),
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model preference order (free tier — best reasoning first)
# IMMUTABLE — see docs/STRATEGY_SPEC.md Section 8.1
OPENROUTER_MODELS = [
    "qwen/qwen-2.5-7b-instruct",
    "google/gemma-2-9b-it",
    "mistralai/mistral-7b-instruct",
]
OPENROUTER_MAX_PROMPT_TOKENS   = 800
OPENROUTER_MAX_RESPONSE_TOKENS = 400

# ── PAPER TRADING PARAMETERS — IMMUTABLE (docs/RISK_SPEC.md) ─────────────────
PAPER_BALANCE          = 18.66
PAPER_RISK_PER_TRADE   = 2.00
PAPER_RISK_CAUTION     = 1.00
PAPER_RISK_MARKET      = 1.00
PAPER_BALANCE_FLOOR    = 14.00

# ── SESSION LIMITS — IMMUTABLE ────────────────────────────────────────────────
MAX_LEVERAGE           = 10
CAUTION_MAX_LEVERAGE   = 5
MAX_TRADES_PER_SESSION = 3
TIME_STOP_HOURS        = 2
HARD_CLOSE_UTC_HOUR    = 20

# ── TWO-LAYER TP STRUCTURE — IMMUTABLE ───────────────────────────────────────
CORE_PCT   = 0.50
RUNNER_PCT = 0.50

# ── VALIDITY WINDOWS — IMMUTABLE ─────────────────────────────────────────────
WINDOW_URGENT_MINS     = 10
WINDOW_PATIENT_MINS    = 30
WINDOW_SET_FORGET_MINS = 60
MAX_CHASE_PCT          = 3.0

# ── BTC REGIME THRESHOLDS — IMMUTABLE ────────────────────────────────────────
BTC_BULL_WHALE_MIN  = 1.05
BTC_BEAR_WHALE_MAX  = 0.95
BTC_STRONG_MIN      = 1.30
BTC_INVALIDATION    = 84200

# ── TRADFI ────────────────────────────────────────────────────────────────────
TRADFI_MIN_WHALE_RATIO = 2.0

# ── QUICK SCAN CONVICTION THRESHOLD — IMMUTABLE ──────────────────────────────
HIGH_CONVICTION_THRESHOLD = {
    "whale_ratio_min":  2.5,
    "fund_side":        "Bullish",
    "volume_min_usd":   5_000_000,
    "price_change_min": 8,
    "price_change_max": 40,
}

# ── PERMANENT SKIP LIST — IMMUTABLE (docs/STRATEGY_SPEC.md Section 3.2) ──────
PERMANENT_SKIP_LIST = [
    "MARSCOIN",
    "LONGXIA",
    "GRAM",
    "DGAI",
    "BR",
    "AKE",
    "VELVET",
    "MELANIA",
    "SOSO",
    "ZEC",
]

# ── TRADFI PERPS — IMMUTABLE (docs/STRATEGY_SPEC.md Section 3.3) ─────────────
TRADFI_PERPS = [
    "COINUSDT",
    "MSTRUSDT",
    "XAUUSDT",
    "NVDAUSDT",
]

# ── STANDING WATCHLIST — IMMUTABLE (docs/STRATEGY_SPEC.md Section 3.4) ───────
WATCHLIST_STANDING = {
    "LINKUSDT": {
        "note":       "BTC mirror — mirrors BTC candle-for-candle, high beta",
        "qualify_if": "whale_ratio > 1.3 AND fund_side == Bullish",
        "skip_if":    "fund_side == Bearish OR whale_ratio <= 1.0",
        "edge":       "amplifies BTC moves 1.2-1.8x",
    },
    "LABUSDT": {
        "note":       "Small cap momentum — high B/S ratio",
        "qualify_if": "whale_ratio >= 3.0 AND fund_side == Bullish",
        "skip_if":    "whale_ratio < 2.0",
        "edge":       "small cap momentum, highest B/S ratio on screener",
    },
    "BEATUSDT": {
        "note":       "Accumulation play — flat price = smart money loading",
        "qualify_if": "whale_ratio >= 4.0 AND price_change_24h < 5",
        "skip_if":    "whale_ratio < 3.0",
        "edge":       "smart money loading quietly before breakout",
    },
    "TAOUSDT": {
        "note":       "AI/Bittensor — strong recurring signal",
        "qualify_if": "whale_ratio >= 2.0 AND top_trader_ratio >= 2.0",
        "skip_if":    "price already ran >8% on the day",
        "edge":       "AI narrative + institutional backing",
    },
    "WIFUSDT": {
        "note":       "Meme with whale backing",
        "qualify_if": "whale_ratio >= 2.0 AND fund_side == Bullish",
        "skip_if":    "daily_change > 15%",
        "edge":       "negative funding = shorts pay longs = squeeze setup",
    },
    "ONDOUSDT": {
        "note":       "RWA narrative — SEC exemption direct catalyst",
        "qualify_if": "whale_ratio >= 1.3 AND fund_side == Bullish",
        "skip_if":    "whale_ratio < 1.1",
        "edge":       "SEC tokenized securities narrative driver",
    },
    "MNTUSDT": {
        "note":       "Mantle — top trader signal strong",
        "qualify_if": "whale_ratio >= 2.5 AND funding < 0.008",
        "skip_if":    "funding >= 0.01",
        "edge":       "top trader ratio often exceeds whale ratio",
    },
}

# ── STOP DISTANCE BOUNDS — IMMUTABLE (docs/STRATEGY_SPEC.md Section 6.1) ──────
# Read by sr_calculator._find_sl() — do NOT change without strategy approval.
MIN_STOP_DIST_PCT = 0.003   # 0.3 % minimum stop distance (below this → widen)
MAX_STOP_DIST_PCT = 0.08    # 8.0 % maximum stop distance (above this → disqualify)

# ── SESSION SKIP LIST — runtime only, cleared on restart ─────────────────────
SESSION_SKIP_LIST: list[str] = []

# ── TASK-026 R1 — Dead Cat Phase 1 drop-volume thresholds ────────────────────
# Used by sr_calculator.dead_cat_check() Phase 1.
DROP_VOL_WEAK_THRESHOLD   = 0.70   # drop_vol_ratio < 0.70 → weak drop (pullback)
DROP_VOL_STRONG_THRESHOLD = 1.30   # drop_vol_ratio > 1.30 → strong dump → block entry
BOUNCE_VOL_MIN_RATIO      = 0.70   # minimum bounce volume vs baseline

# ── TASK-026 R5 — Quick scan executor cap ─────────────────────────────────────
QUICKSCAN_MAX_WORKERS = 8

# ── TASK-026 R4 — Email fallback (Telegram failure) ───────────────────────────
# Set EMAIL_FALLBACK_ENABLED=True in .env before GATE-3.
# ALL credentials must be set via environment variables — no hardcoded defaults.
EMAIL_FALLBACK_ENABLED = False
EMAIL_FROM    = os.getenv("EMAIL_FROM", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# ── TASK-026 R6 — Funding creep alert threshold ───────────────────────────────
# Alert fires once per order when abs(funding) >= this threshold.
CROWDED_FUNDING_THRESHOLD = 0.0005   # 0.05% = crowded funding
