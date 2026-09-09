import runtime_bootstrap


def test_present_invalid_config_is_unavailable(monkeypatch, tmp_path):
    class Bridge:
        def __init__(self, config): pass
        def request(self, operation): raise RuntimeError("invalid yaml")
    monkeypatch.setattr(runtime_bootstrap, "OdooTuiWorkerBridge", Bridge)
    monkeypatch.setattr(runtime_bootstrap, "INTERPRETER_PATH", tmp_path / "python")
    monkeypatch.setattr(runtime_bootstrap, "WORKER_PATH", tmp_path / "worker.py")
    (tmp_path / "python").write_text("", encoding="utf-8")
    (tmp_path / "worker.py").write_text("", encoding="utf-8")
    runtime_bootstrap.reset_for_tests()
    state = runtime_bootstrap.bootstrap_adapter()
    assert state.ok is False
    assert state.code == "ADAPTER_UNAVAILABLE"
