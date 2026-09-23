"""Telegram Bot API wrapper.
Never raises — returns bool success/failure on all methods.
Silently logs errors. Caller decides how to handle False return.
"""

import logging
from typing import Optional

import requests

from bybit_bot.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    EMAIL_FALLBACK_ENABLED,
    EMAIL_FROM,
    EMAIL_TO,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
)

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


def send_message_with_fallback(text: str) -> bool:
    """Send via Telegram; fall back to email if Telegram fails and fallback is enabled.

    Use this function for CRITICAL alerts (SL hit, TP1, time-stop, hard close,
    BTC invalidation) so the operator is notified even during Telegram outages.
    Use plain ``send_message()`` for informational messages.

    Returns:
        True if either Telegram or email delivery succeeded, False if both failed.
    """
    success = send_message(text)
    if not success and EMAIL_FALLBACK_ENABLED:
        return _send_email_fallback(
            subject="[Bybit Bot] TELEGRAM FAILED — URGENT",
            body=text,
        )
    return success


def _send_email_fallback(subject: str, body: str) -> bool:
    """Send an email via SMTP as a Telegram fallback channel.

    Credentials are loaded from .env via config.py — never hardcoded.
    Returns True on successful delivery, False on any failure.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage

    try:
        msg = EmailMessage()
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(context=ctx)
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        logger.info("email_fallback_sent subject=%r", subject)
        return True
    except Exception as exc:
        logger.error("email_fallback_failed exc=%s", exc)
        return False
