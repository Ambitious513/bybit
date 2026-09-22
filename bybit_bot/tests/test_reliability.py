"""TASK-024 tests for Telegram recovery, durable offsets, and research locking."""

import json
import threading

import pytest


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    """Provide the environment expected by orchestrator imports."""
    monkeypatch.setenv("BYBIT_API_KEY", "test_key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCD")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("OPENROUTER_KEY_1", "or_key_1")
    monkeypatch.setenv("OPENROUTER_KEY_2", "or_key_2")
    monkeypatch.setenv("OPENROUTER_KEY_3", "or_key_3")


def _stop_polling(*args, **kwargs):
    """End a deliberately bounded poll-loop test without being caught as an Exception."""
    raise KeyboardInterrupt


def test_offset_loaded_on_startup(tmp_path, monkeypatch):
    """The poller starts from the durable next-update offset, not zero."""
    from bybit_bot import orchestrator

    offset_path = tmp_path / "tg_offset.json"
    offset_path.write_text(json.dumps({"offset": 50}), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_bot_data_path", lambda filename: str(tmp_path / filename))
    seen: list[int] = []
    monkeypatch.setattr(
        orchestrator.telegram,
        "get_updates",
        lambda offset, timeout: seen.append(offset) or _stop_polling(),
    )

    with pytest.raises(KeyboardInterrupt):
        orchestrator._telegram_poll_loop()
    assert seen == [50]


def test_offset_saved_after_each_update(tmp_path, monkeypatch):
    """Each acknowledged update advances and persists the offset independently."""
    from bybit_bot import orchestrator

    monkeypatch.setattr(orchestrator, "_bot_data_path", lambda filename: str(tmp_path / filename))
    updates = [[{"update_id": 10}, {"update_id": 11}, {"update_id": 12}]]
    saved: list[int] = []
    monkeypatch.setattr(orchestrator.telegram, "get_updates", lambda **kwargs: updates.pop(0) if updates else _stop_polling())
    monkeypatch.setattr(orchestrator, "_dispatch_telegram_update", lambda update: None)
    monkeypatch.setattr(orchestrator, "_save_tg_offset", lambda offset: saved.append(offset))

    with pytest.raises(KeyboardInterrupt):
        orchestrator._telegram_poll_loop()
    assert saved == [11, 12, 13]


def test_malformed_update_does_not_stop_polling(monkeypatch):
    """A dispatch failure is isolated and the following update still runs."""
    from bybit_bot import orchestrator

    updates = [[{"update_id": 1}, {"update_id": 2}]]
    dispatched: list[int] = []

    def dispatch(update):
        if update["update_id"] == 1:
            raise ValueError("malformed")
        dispatched.append(update["update_id"])

    monkeypatch.setattr(orchestrator.telegram, "get_updates", lambda **kwargs: updates.pop(0) if updates else _stop_polling())
    monkeypatch.setattr(orchestrator, "_dispatch_telegram_update", dispatch)
    monkeypatch.setattr(orchestrator, "_save_tg_offset", lambda offset: None)

    with pytest.raises(KeyboardInterrupt):
        orchestrator._telegram_poll_loop()
    assert dispatched == [2]


def test_poll_exception_triggers_backoff(monkeypatch):
    """A polling failure waits for the current exponential-backoff interval."""
    from bybit_bot import orchestrator

    sleeps: list[int] = []
    monkeypatch.setattr(orchestrator.telegram, "get_updates", lambda **kwargs: (_ for _ in ()).throw(OSError("network")))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda seconds: sleeps.append(seconds) or _stop_polling())

    with pytest.raises(KeyboardInterrupt):
        orchestrator._telegram_poll_loop()
    assert sleeps == [1]


def test_poll_recovers_after_exception(monkeypatch, caplog):
    """A successful poll after failure resets backoff and logs recovery."""
    from bybit_bot import orchestrator

    calls = 0
    sleeps: list[int] = []

    def get_updates(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("network")
        return []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            _stop_polling()

    monkeypatch.setattr(orchestrator.telegram, "get_updates", get_updates)
    monkeypatch.setattr(orchestrator.time, "sleep", sleep)
    caplog.set_level("WARNING", logger="orchestrator")

    with pytest.raises(KeyboardInterrupt):
        orchestrator._telegram_poll_loop()
    assert calls == 2
    assert sleeps == [1, 1]
    assert "telegram_poll_resumed" in caplog.text


def test_concurrent_research_runs_once(monkeypatch):
    """Two real concurrent scheduler calls permit only the lock winner to run research."""
    from bybit_bot import orchestrator, research

    monkeypatch.setattr(orchestrator, "_RESEARCH_LOCK", threading.Lock())
    started = threading.Event()
    release = threading.Event()
    runs: list[bool] = []

    def run_research(**kwargs):
        runs.append(True)
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(research, "run_research", run_research)
    first = threading.Thread(target=orchestrator.cmd_research)
    second = threading.Thread(target=orchestrator.cmd_research)
    first.start()
    assert started.wait(timeout=2)
    second.start()
    second.join(timeout=2)
    release.set()
    first.join(timeout=2)

    assert runs == [True]


def test_research_command_blocked_when_research_running(monkeypatch):
    """The Telegram path informs the operator instead of starting overlapping research."""
    from bybit_bot import orchestrator

    monkeypatch.setattr(orchestrator, "_RESEARCH_LOCK", threading.Lock())
    messages: list[str] = []
    monkeypatch.setattr(orchestrator.telegram, "send_message", messages.append)
    assert orchestrator._RESEARCH_LOCK.acquire(blocking=False)
    try:
        orchestrator.handle_research()
    finally:
        orchestrator._RESEARCH_LOCK.release()
    assert messages == ["🔍 Research already in progress — card incoming shortly"]


def test_heartbeat_sends_expected_fields(tmp_path, monkeypatch):
    """The daily heartbeat reports health, active-order count, and paper balance."""
    from bybit_bot import orchestrator

    (tmp_path / "research_cache.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_bot_data_path", lambda filename: str(tmp_path / filename))
    monkeypatch.setattr(orchestrator, "_load_balance", lambda: {"balance": 17.25})
    monkeypatch.setattr(orchestrator, "load_active_orders", lambda: [
        {"status": "PENDING"}, {"status": "FILLED"}, {"status": "CLOSED"},
    ])
    messages: list[str] = []
    monkeypatch.setattr(orchestrator.telegram, "send_message", messages.append)

    orchestrator._heartbeat()
    assert "alive" in messages[0]
    assert "Balance: $17.25" in messages[0]
    assert "Active orders: 2" in messages[0]
