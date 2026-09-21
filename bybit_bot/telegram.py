"""Telegram Bot API wrapper.
Never raises — returns bool success/failure on all methods.
Silently logs errors. Caller decides how to handle False return.
"""

import logging
from typing import Optional

import requests

from bybit_bot.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("telegram")

_MAX_LENGTH = 4096
_TRUNCATION_SUFFIX = "...[truncated]"


def _base_url() -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _truncate(text: str) -> str:
    """Truncate message to Telegram's 4096 char limit."""
    if len(text) <= _MAX_LENGTH:
        return text
    return text[: _MAX_LENGTH - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def send_message(text: str, chat_id: Optional[str] = None) -> bool:
    """Send an HTML-formatted message to the configured Telegram chat.

    Args:
        text:    Message text (HTML parse mode).
        chat_id: Override chat ID (defaults to TELEGRAM_CHAT_ID).

    Returns:
        True on success, False on any failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("telegram_not_configured — missing token or chat_id")
        return False

    payload = {
        "chat_id":    chat_id or TELEGRAM_CHAT_ID,
        "text":       _truncate(text),
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(
            f"{_base_url()}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.error("telegram_send_failed status=%d body=%s", resp.status_code, resp.text[:200])
        return False
    except requests.exceptions.RequestException as exc:
        logger.error("telegram_request_error error=%s", exc)
        return False


def send_card(lines: list[str]) -> bool:
    """Send a monospace-formatted card to Telegram.

    Wraps lines in <pre> tags for clean alignment in Telegram.

    Args:
        lines: List of strings, one per line.

    Returns:
        True on success, False on any failure.
    """
    content = "\n".join(lines)
    text = f"<pre>{_truncate(content)}</pre>"
    return send_message(text)


def get_updates(offset: int = 0, timeout: int = 30) -> list[dict]:
    """Long-poll for new Telegram updates.

    Args:
        offset:  Exclude updates before this ID.
        timeout: Long-poll timeout in seconds.

    Returns:
        List of update dicts. Empty list on failure.
    """
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        resp = requests.get(
            f"{_base_url()}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 5,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
        logger.error("get_updates_failed status=%d", resp.status_code)
        return []
    except requests.exceptions.RequestException as exc:
        logger.error("get_updates_error error=%s", exc)
        return []
