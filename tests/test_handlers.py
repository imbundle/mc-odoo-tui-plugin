import time

from handlers import restart_instance, start_instance, stop_instance


class Boundary:
    def __init__(self, state="stopped", result=None, error=None):
        self.current = state
        self.result = result
        self.error = error
        self.calls = []

    def status(self, client):
        self.calls.append(("status", client))
        return {"state": self.current}

    def start(self, client):
        self.calls.append(("start", client))
        if self.error:
            raise self.error
        if self.result is not None:
            self.current = self.result
        else:
            self.current = "running"

    def stop(self, client):
        self.calls.append(("stop", client))
        self.current = "stopped"

    def restart(self, client):
        self.calls.append(("restart", client))
        self.current = "running"


def test_denied_approval_does_not_mutate():
    boundary = Boundary()
    result = start_instance("demo", boundary, lambda *_: False)
    assert result["code"] == "approval_denied"
    assert [name for name, _ in boundary.calls] == ["status"]


def test_start_success_requires_readback():
    boundary = Boundary()
    result = start_instance("demo", boundary, lambda *_: True)
    assert result["ok"] is True
    assert [name for name, _ in boundary.calls] == ["status", "start", "status"]


def test_readback_mismatch_is_not_success():
    boundary = Boundary(result="stopped")
    result = start_instance("demo", boundary, lambda *_: True)
    assert result["code"] == "readback_mismatch"
    assert result["ok"] is False


def test_process_failure_is_reported_without_claiming_success():
    boundary = Boundary(error=RuntimeError("pm2 failed"))
    result = start_instance("demo", boundary, lambda *_: True)
    assert result["code"] == "process_failure"
    assert result["ok"] is False


def test_timeout_is_bounded():
    class Slow(Boundary):
        def start(self, client):
            time.sleep(0.2)

    boundary = Slow()
    began = time.monotonic()
    result = start_instance("demo", boundary, lambda *_: True, timeout_seconds=0.01)
    assert result["code"] == "timeout"
    assert time.monotonic() - began < 0.15


def test_stop_and_restart_enforce_confirmed_states():
    stopped = Boundary("stopped")
    assert stop_instance("demo", stopped, lambda *_: True)["code"] == "invalid_state"
    running = Boundary("running")
    assert restart_instance("demo", running, lambda *_: True)["ok"] is True


def test_stop_success_reads_back_stopped_state():
    boundary = Boundary("running")

    result = stop_instance("demo", boundary, lambda *_: True)

    assert result == {"ok": True, "code": "stop", "client": "demo", "state": "stopped"}
    assert [name for name, _ in boundary.calls] == ["status", "stop", "status"]


def test_restart_failure_does_not_claim_running():
    class FailingRestart(Boundary):
        def restart(self, client):
            self.calls.append(("restart", client))
            raise RuntimeError("pm2 unavailable")

    boundary = FailingRestart("running")
    result = restart_instance("demo", boundary, lambda *_: True)

    assert result["ok"] is False
    assert result["code"] == "process_failure"
    assert result["state"] == "running"


def test_database_status_failure_blocks_lifecycle_action():
    class DatabaseUnavailable(Boundary):
        def status(self, client):
            self.calls.append(("status", client))
            raise ConnectionError("postgres password leaked")

    boundary = DatabaseUnavailable()
    result = start_instance("demo", boundary, lambda *_: True)

    assert result == {"ok": False, "code": "status_failure", "client": "demo"}
    assert [name for name, _ in boundary.calls] == ["status"]
    assert "password" not in str(result)
