import json
import os
import sys
import time
from pathlib import Path

import pytest

from operation_bridge import BridgeError, OperationBridge, OperationBridgeConfig


def config(worker: Path, **kwargs):
    return OperationBridgeConfig(
        interpreter=Path(sys.executable),
        worker=worker,
        config_path=Path("/tmp/odoo-tui-test-config.yaml"),
        **kwargs,
    )


def request(operation="lifecycle.start", **extra):
    return {
        "protocol_version": 1,
        "operation": operation,
        "client": "acme",
        "environment": "local",
        **extra,
    }


def test_invalid_request_is_rejected_before_worker_spawn(tmp_path, monkeypatch):
    bridge = OperationBridge(config(tmp_path / "unused.py"))
    monkeypatch.setattr("operation_bridge.subprocess.Popen", lambda *a, **k: pytest.fail("worker must not start"))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme", argv=[]))
    assert error.value.code == "INVALID_REQUEST"


def test_mutex_contention_returns_409_code_without_queue(tmp_path, monkeypatch):
    bridge = OperationBridge(config(tmp_path / "unused.py"))
    assert bridge._mutex.acquire(blocking=False)
    try:
        monkeypatch.setattr("operation_bridge.subprocess.Popen", lambda *a, **k: pytest.fail("worker must not start"))
        with pytest.raises(BridgeError) as error:
            bridge.invoke(request("lifecycle.start", confirmation="START acme"))
        assert error.value.code == "OPERATION_IN_PROGRESS"
    finally:
        bridge._mutex.release()


def test_worker_success_round_trips_strict_envelope(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,sys,os\n"
        "r=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'protocol_version':1,'operation':r['operation'],'ok':True,'data':{'state':'online','control_mode':'client','session':os.getsid(0)==os.getpid()}}))\n",
        encoding="utf-8",
    )
    bridge = OperationBridge(config(worker, lifecycle_timeout_seconds=1))
    assert bridge.invoke(request("lifecycle.start", confirmation="START acme"))["data"] == {
        "state": "online",
        "control_mode": "client",
        "session": True,
    }


def test_worker_error_is_typed_and_redacted(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json\n"
        "print(json.dumps({'protocol_version':1,'operation':'lifecycle.start','ok':False,'error':{'code':'OPERATION_FAILED','message':'secret/path'}}))\n",
        encoding="utf-8",
    )
    bridge = OperationBridge(config(worker, lifecycle_timeout_seconds=1))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme"))
    assert error.value.code == "OPERATION_FAILED"
    assert "secret" not in error.value.message


def test_request_size_limit_is_enforced(tmp_path):
    bridge = OperationBridge(config(tmp_path / "unused.py"))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme" + "x" * 20000))
    assert error.value.code == "INVALID_REQUEST"


def test_timeout_kills_the_worker_process_group(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    bridge = OperationBridge(config(worker, lifecycle_timeout_seconds=0.05, reconcile_timeout_seconds=0.05, term_grace_seconds=0.05, reap_timeout_seconds=0.05))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme"))
    assert error.value.code == "OPERATION_TIMED_OUT"
    assert bridge._mutex.acquire(blocking=False)
    bridge._mutex.release()


def test_streaming_output_cap_stops_worker(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("print('x' * 70000, flush=True)\n", encoding="utf-8")
    bridge = OperationBridge(config(worker, lifecycle_timeout_seconds=1, term_grace_seconds=0.05, reap_timeout_seconds=0.05))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme"))
    assert error.value.code == "WORKER_PROTOCOL_ERROR"


def test_sigterm_resistant_descendant_is_killed(tmp_path):
    worker = tmp_path / "worker.py"
    pid_file = tmp_path / "child.pid"
    worker.write_text(
        "import json,os,signal,subprocess,sys,time\n"
        "request=json.loads(sys.stdin.readline())\n"
        f"if request['operation']=='runtime.reconcile': print(json.dumps({{'protocol_version':1,'operation':'runtime.reconcile','ok':True,'data':{{'state':'unknown','control_mode':'unknown','database_exists':None}}}}),flush=True)\n"
        f"else: child=subprocess.Popen([sys.executable,'-c',\"import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open(r'{pid_file}','w').write(str(os.getpid())); time.sleep(10)\"]); time.sleep(10)\n",
        encoding="utf-8",
    )
    bridge = OperationBridge(config(worker, lifecycle_timeout_seconds=0.05, reconcile_timeout_seconds=0.05, term_grace_seconds=0.05, reap_timeout_seconds=0.05))
    with pytest.raises(BridgeError) as error:
        bridge.invoke(request("lifecycle.start", confirmation="START acme"))
    assert error.value.code == "OPERATION_TIMED_OUT"
    deadline = time.monotonic() + 1
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
