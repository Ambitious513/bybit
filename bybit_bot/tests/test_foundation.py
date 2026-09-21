"""TASK-016 — Foundation tests.
All Bybit HTTP calls are mocked. No live API calls in tests.
"""

import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Ensure bybit_bot package is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Set required env vars for every test."""
    monkeypatch.setenv("BYBIT_API_KEY",      "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET",   "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID",   "999")
    monkeypatch.setenv("OPENROUTER_KEY_1",   "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2",   "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3",   "or_key_3")


def _mock_response(status: int, body: dict) -> MagicMock:
    """Build a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = body
    mock.text = json.dumps(body)
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# bybit_api tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBybitApi:
    def test_get_ticker_returns_valid_shape(self):
        """Mock HTTP 200 → ticker dict with 5 required keys."""
        from bybit_bot import bybit_api
        body = {
            "retCode": 0,
            "result": {
                "list": [{
                    "symbol":       "BTCUSDT",
                    "lastPrice":    "65000.00",
                    "price24hPcnt": "0.0250",
                    "turnover24h":  "1234567890",
                    "highPrice24h": "66000.00",
                    "lowPrice24h":  "64000.00",
                }]
            }
        }
        with patch("bybit_bot.bybit_api._SESSION") as mock_sess:
            mock_sess.get.return_value = _mock_response(200, body)
            result = bybit_api.get_ticker("BTCUSDT")

        assert result is not None
        assert "price" in result
        assert "price24hPcnt" in result
        assert "turnover24h" in result
        assert "high24h" in result
        assert "low24h" in result

    def test_get_ticker_returns_none_on_500(self):
        """HTTP 500 (all retries) → None returned, no exception raised."""
        from bybit_bot import bybit_api
        with patch("bybit_bot.bybit_api._SESSION") as mock_sess:
            mock_sess.get.return_value = _mock_response(500, {})
            with patch("bybit_bot.bybit_api.time") as mock_time:
                mock_time.sleep = MagicMock()
                result = bybit_api.get_ticker("BTCUSDT")

        assert result is None

    def test_get_ticker_returns_none_on_bybit_error(self):
        """Bybit retCode != 0 → None returned."""
        from bybit_bot import bybit_api
        body = {"retCode": 10001, "retMsg": "params error", "result": {}}
        with patch("bybit_bot.bybit_api._SESSION") as mock_sess:
            mock_sess.get.return_value = _mock_response(200, body)
            result = bybit_api.get_ticker("INVALID")

        assert result is None

    def test_get_all_linear_tickers_returns_list(self):
        """Multiple tickers returned as list of dicts."""
        from bybit_bot import bybit_api
        body = {
            "retCode": 0,
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "lastPrice": "65000", "price24hPcnt": "0.02",
                     "turnover24h": "1e9", "highPrice24h": "66000", "lowPrice24h": "64000"},
                    {"symbol": "ETHUSDT", "lastPrice": "3000", "price24hPcnt": "0.01",
                     "turnover24h": "5e8", "highPrice24h": "3100", "lowPrice24h": "2900"},
                ]
            }
        }
        with patch("bybit_bot.bybit_api._SESSION") as mock_sess:
            mock_sess.get.return_value = _mock_response(200, body)
            results = bybit_api.get_all_linear_tickers()

        assert len(results) == 2
        assert results[0]["symbol"] == "BTCUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# data_aggregator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDataAggregator:
    def _ls(self, buy="0.60", sell="0.40"):
        return {"buyRatio": buy, "sellRatio": sell, "timestamp": "1000"}

    def _top(self, buy="0.65", sell="0.35"):
        return {"buyRatio": buy, "sellRatio": sell}

    def _fund(self, rate="0.0001"):
        return {"fundingRate": rate, "fundingRateTimestamp": "1000"}

    def _oi(self, value="1000000"):
        return {"openInterest": value, "timestamp": "1000"}

    @pytest.fixture
    def tmp_oi_path(self, tmp_path, monkeypatch):
        """Redirect OI history to a temp directory."""
        oi_file = tmp_path / "oi_history.json"
        monkeypatch.setattr("bybit_bot.data_aggregator._OI_HISTORY_PATH", str(oi_file))
        return oi_file

    def test_fund_side_bullish_when_rate_positive(self, tmp_oi_path):
        """Positive funding rate → fund_side == Bullish."""
        from bybit_bot import data_aggregator
        with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=self._fund("0.0001")), \
             patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
            result = data_aggregator.get_capital_flow("BTCUSDT")

        assert result is not None
        assert result["fundSide"] == "Bullish"

    def test_fund_side_bearish_when_rate_negative(self, tmp_oi_path):
        """Negative funding rate → fund_side == Bearish."""
        from bybit_bot import data_aggregator
        with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=self._fund("-0.0001")), \
             patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
            result = data_aggregator.get_capital_flow("BTCUSDT")

        assert result is not None
        assert result["fundSide"] == "Bearish"

    def test_whale_ratio_calculation(self, tmp_oi_path):
        """buyRatio=0.6, sellRatio=0.4 → longShortRatio = 1.5."""
        from bybit_bot import data_aggregator
        with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls("0.60", "0.40")), \
             patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=self._fund()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
            result = data_aggregator.get_capital_flow("BTCUSDT")

        assert result is not None
        assert result["longShortRatio"] == pytest.approx(1.5, rel=1e-3)

    def test_returns_none_when_funding_fails(self, tmp_oi_path):
        """Funding endpoint failure → None returned."""
        from bybit_bot import data_aggregator
        with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=None), \
             patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
            result = data_aggregator.get_capital_flow("BTCUSDT")

        assert result is None

    def test_oi_history_accumulates(self, tmp_oi_path):
        """Two calls produce two entries in oi_history.json."""
        from bybit_bot import data_aggregator
        for _ in range(2):
            with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls()), \
                 patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
                 patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=self._fund()), \
                 patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
                data_aggregator.get_capital_flow("BTCUSDT")

        history = json.loads(tmp_oi_path.read_text())
        assert len(history["BTCUSDT"]) == 2

    def test_thirty_days_ago_none_when_insufficient_history(self, tmp_oi_path):
        """< 30 days of history → thirtyDaysAgo is None."""
        from bybit_bot import data_aggregator
        with patch("bybit_bot.data_aggregator.bybit_api.get_long_short_ratio", return_value=self._ls()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_top_trader_ratio",  return_value=self._top()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_funding_rate",      return_value=self._fund()), \
             patch("bybit_bot.data_aggregator.bybit_api.get_open_interest",     return_value=self._oi()):
            result = data_aggregator.get_capital_flow("BTCUSDT")

        assert result is not None
        assert result["openInterestHistory"]["thirtyDaysAgo"] is None


# ─────────────────────────────────────────────────────────────────────────────
# telegram tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTelegram:
    def test_send_message_returns_true_on_200(self):
        """HTTP 200 from Telegram → True."""
        from bybit_bot import telegram
        with patch("bybit_bot.telegram.requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"ok": True})
            result = telegram.send_message("Hello")
        assert result is True

    def test_send_message_returns_false_on_error(self):
        """Non-200 response → False."""
        from bybit_bot import telegram
        with patch("bybit_bot.telegram.requests.post") as mock_post:
            mock_post.return_value = _mock_response(400, {"ok": False})
            result = telegram.send_message("Hello")
        assert result is False

    def test_long_message_is_truncated(self):
        """Message > 4096 chars gets truncated before sending."""
        from bybit_bot import telegram
        long_text = "A" * 5000
        with patch("bybit_bot.telegram.requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"ok": True})
            telegram.send_message(long_text)
            payload = mock_post.call_args[1]["json"]
        assert len(payload["text"]) <= 4096
        assert payload["text"].endswith("...[truncated]")

    def test_send_card_wraps_in_pre_tags(self):
        """send_card wraps content in <pre> tags."""
        from bybit_bot import telegram
        with patch("bybit_bot.telegram.requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"ok": True})
            telegram.send_card(["line1", "line2"])
            payload = mock_post.call_args[1]["json"]
        assert "<pre>" in payload["text"]
        assert "line1" in payload["text"]


# ─────────────────────────────────────────────────────────────────────────────
# openrouter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenRouter:
    def test_returns_string_on_success(self):
        """Successful API response → string returned."""
        from bybit_bot import openrouter
        body = {"choices": [{"message": {"content": "Hello World"}}]}
        with patch("bybit_bot.openrouter.requests.post") as mock_post:
            mock_post.return_value = _mock_response(200, body)
            result = openrouter.complete("Say hello")
        assert result == "Hello World"

    def test_key_rotation_on_429(self):
        """429 on first key → rotates to next key on retry."""
        from bybit_bot import openrouter
        success_body = {"choices": [{"message": {"content": "OK"}}]}
        responses = [
            _mock_response(429, {}),
            _mock_response(200, success_body),
        ]
        with patch("bybit_bot.openrouter.requests.post", side_effect=responses):
            result = openrouter.complete("ping")
        assert result == "OK"

    def test_returns_none_when_no_keys(self, monkeypatch):
        """No API keys configured → None returned gracefully."""
        from bybit_bot import openrouter
        monkeypatch.setattr("bybit_bot.openrouter.OPENROUTER_KEYS", [None, None, None])
        result = openrouter.complete("test")
        assert result is None

    def test_parse_json_response_strips_markdown(self):
        """JSON wrapped in markdown fences is parsed correctly."""
        from bybit_bot import openrouter
        raw = '```json\n{"sentiment": "BULLISH"}\n```'
        result = openrouter.parse_json_response(raw)
        assert result == {"sentiment": "BULLISH"}

    def test_parse_json_response_returns_none_on_invalid(self):
        """Non-JSON response → None."""
        from bybit_bot import openrouter
        result = openrouter.parse_json_response("not json at all")
        assert result is None
