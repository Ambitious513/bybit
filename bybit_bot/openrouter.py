"""OpenRouter LLM wrapper with round-robin API key rotation.
Used ONLY for news sentiment classification — never for trading math.
Returns None gracefully on all failures — caller must handle None.
"""

import json
import logging
from typing import Optional

import requests

from bybit_bot.config import (
    OPENROUTER_BASE_URL,
    OPENROUTER_KEYS,
    OPENROUTER_MAX_PROMPT_TOKENS,
    OPENROUTER_MAX_RESPONSE_TOKENS,
    OPENROUTER_MODELS,
)

logger = logging.getLogger("openrouter")

# Module-level key index — round-robin across calls
_current_key_index: int = 0


def _next_key() -> Optional[str]:
    """Advance to next key and return it. Returns None if all keys exhausted."""
    global _current_key_index
    valid_keys = [k for k in OPENROUTER_KEYS if k]
    if not valid_keys:
        return None
    key = valid_keys[_current_key_index % len(valid_keys)]
    _current_key_index = (_current_key_index + 1) % len(valid_keys)
    return key


def complete(prompt: str, system_prompt: str = "") -> Optional[str]:
    """Send a chat completion request to OpenRouter.

    Rotates keys on every call (round-robin).
    On 429 or quota error: increments key index and retries immediately.
    On model error: falls back through OPENROUTER_MODELS list.
    On all keys exhausted: logs warning and returns None.

    Args:
        prompt:        User message content.
        system_prompt: Optional system instruction. Request JSON output here.

    Returns:
        String response content or None on failure.
    """
    valid_keys = [k for k in OPENROUTER_KEYS if k]
    if not valid_keys:
        logger.warning("openrouter_no_keys_configured")
        return None

    # Try each key once per model attempt
    for key_attempt in range(len(valid_keys)):
        key = _next_key()
        if not key:
            break

        for model in OPENROUTER_MODELS:
            try:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})

                resp = requests.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":      model,
                        "messages":   messages,
                        "max_tokens": OPENROUTER_MAX_RESPONSE_TOKENS,
                    },
                    timeout=15,
                )

                if resp.status_code == 429:
                    logger.warning("openrouter_rate_limited key_attempt=%d model=%s", key_attempt, model)
                    break  # Try next key

                if resp.status_code != 200:
                    logger.warning("openrouter_error status=%d model=%s", resp.status_code, model)
                    continue  # Try next model

                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                logger.info("openrouter_success model=%s key_attempt=%d", model, key_attempt)
                return content

            except (requests.exceptions.RequestException, KeyError, IndexError) as exc:
                logger.error("openrouter_exception model=%s error=%s", model, exc)
                continue

    logger.warning("openrouter_all_keys_exhausted — falling back to no-LLM mode")
    return None


def parse_json_response(response: Optional[str]) -> Optional[dict]:
    """Safely parse a JSON string from an LLM response.

    Handles responses wrapped in markdown code fences.
    Returns None if parsing fails.
    """
    if not response:
        return None
    text = response.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("openrouter_json_parse_failed error=%s response_preview=%s",
                       exc, text[:100])
        return None
