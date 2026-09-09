from pathlib import Path
from types import SimpleNamespace

import handlers


class ReadAdapter:
    def __init__(self, state: str):
        self.state = state

    def get_status(self, client: str):
        return SimpleNamespace(state=self.state)


def configure_log_handler(monkeypatch, tmp_path: Path, state: str) -> Path:
    path = tmp_path / "odoo.log"
    handlers.clear_log_sources()
    handlers.end_update_log_window("acme")
    handlers.register_log_source("acme", path)
    monkeypatch.setattr(handlers, "_require_registered_client", lambda client: None)
    monkeypatch.setattr(handlers, "_read", lambda call: call(ReadAdapter(state)))
    return path


def test_stopped_client_has_an_empty_log_window(monkeypatch, tmp_path: Path):
    path = configure_log_handler(monkeypatch, tmp_path, "stopped")
    path.write_text("INFO historical line\n", encoding="utf-8")

    result = handlers.poll_logs(client="acme", limit=100)

    assert result == {"entries": [], "next_cursor": None, "has_more": False, "cursor_reset": False}


def test_stopped_client_log_is_visible_during_active_update(monkeypatch, tmp_path: Path):
    path = configure_log_handler(monkeypatch, tmp_path, "stopped")
    path.write_text("INFO loading parkair_account\n", encoding="utf-8")
    handlers.begin_update_log_window("acme")

    try:
        result = handlers.poll_logs(client="acme", limit=100)
    finally:
        handlers.end_update_log_window("acme")

    assert [entry["message"] for entry in result["entries"]] == ["loading parkair_account"]


def test_online_client_starts_at_the_current_log_tail(monkeypatch, tmp_path: Path):
    path = configure_log_handler(monkeypatch, tmp_path, "online")
    path.write_text(
        "INFO historical one\nINFO historical two\nINFO current one\nINFO current two\n",
        encoding="utf-8",
    )

    result = handlers.poll_logs(client="acme", limit=2)

    assert [entry["message"] for entry in result["entries"]] == ["current one", "current two"]
