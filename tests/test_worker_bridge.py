import json
import sys
from pathlib import Path

import pytest

from worker_bridge import BridgeError, OdooTuiWorkerBridge, WorkerConfig


@pytest.fixture
def worker_script(tmp_path: Path):
    def create(source: str) -> Path:
        path = tmp_path / "worker.py"
        path.write_text(source, encoding="utf-8")
        return path
    return create


def bridge(worker: Path, *, timeout: float = 1.0) -> OdooTuiWorkerBridge:
    return OdooTuiWorkerBridge(WorkerConfig(interpreter=Path(sys.executable), worker=worker, timeout_seconds=timeout))


def test_fixture_request_round_trips(worker_script):
    worker = worker_script("import json, sys\nrequest=json.loads(sys.stdin.readline())\nprint(json.dumps({'protocol_version':1,'operation':request['operation'],'ok':True,'data':{'status':{'state':'online'}}}))\n")
    assert bridge(worker).invoke(operation="runtime.status", client="demo") == {
        "protocol_version": 1, "operation": "runtime.status", "ok": True, "data": {"status": {"state": "online"}}
    }


def test_adapter_transport_seam_is_read_only(worker_script):
    worker = worker_script("import json, sys\nrequest=json.loads(sys.stdin.readline())\nprint(json.dumps({'protocol_version':1,'operation':request['operation'],'ok':True,'data':{'status':{'state':'stopped'}}}))\n")
    assert bridge(worker).request("runtime.status", client="demo") == {"status": {"state": "stopped"}}


def test_disallowed_operation_is_rejected_before_process_execution(worker_script, monkeypatch):
    monkeypatch.setattr("worker_bridge.subprocess.Popen", lambda *a, **k: pytest.fail("worker must not start"))
    with pytest.raises(BridgeError, match="allowlisted") as error:
        bridge(worker_script("raise SystemExit(99)\n")).invoke(operation="start", client="demo", environment="local")
    assert error.value.code == "OPERATION_NOT_ALLOWED"


def test_fixed_interpreter_is_used(worker_script, monkeypatch):
    worker = worker_script("import json, sys\nrequest=json.loads(sys.stdin.readline())\nprint(json.dumps({'protocol_version':1,'operation':request['operation'],'ok':True,'data':{'status':{'state':'online'}}}))\n")
    calls = []
    real_popen = __import__("worker_bridge").subprocess.Popen
    def capture(*args, **kwargs):
        calls.append((args, kwargs)); return real_popen(*args, **kwargs)
    monkeypatch.setattr("worker_bridge.subprocess.Popen", capture)
    bridge(worker).invoke(operation="runtime.status", client="demo")
    assert calls[0][0][0][0] == sys.executable and calls[0][0][0][1] == str(worker)
    assert calls[0][1]["shell"] is False


def test_configured_interpreter_and_bounded_path_are_used(worker_script):
    worker = worker_script("import json, os, sys\nrequest=json.loads(sys.stdin.readline())\nprint(json.dumps({'protocol_version':1,'operation':request['operation'],'ok':True,'data':{'executable':sys.executable,'path':os.environ['PATH']}}))\n")
    response = bridge(worker).invoke(operation="runtime.status", client="demo")
    assert response["data"]["executable"] == sys.executable
    assert response["data"]["path"] == "/usr/bin:/bin"


@pytest.mark.parametrize(("source", "code"), [
    ("import time\ntime.sleep(2)\n", "TIMEOUT"),
    ("import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n", "PROCESS_CRASH"),
    ("print('not json')\n", "MALFORMED_RESPONSE"),
    ("import sys\nsys.exit(4)\n", "PROCESS_CRASH"),
])
def test_failures_have_deterministic_codes(worker_script, source, code):
    with pytest.raises(BridgeError) as error:
        bridge(worker_script(source), timeout=0.05).invoke(operation="runtime.status", client="demo")
    assert error.value.code == code and error.value.message


@pytest.mark.parametrize("response", [
    "[]", "{\"protocol_version\":2,\"operation\":\"runtime.status\",\"ok\":true,\"data\":{}}",
    "{\"protocol_version\":1,\"operation\":\"start\",\"ok\":true,\"data\":{}}",
    "{\"protocol_version\":1,\"operation\":\"runtime.status\",\"ok\":true}",
    "{\"protocol_version\":1,\"operation\":\"runtime.status\",\"ok\":false,\"error\":{}}",
])
def test_schema_invalid_worker_responses_are_rejected(worker_script, response):
    with pytest.raises(BridgeError) as error:
        bridge(worker_script(f"print({response!r})\n")).invoke(operation="runtime.status", client="demo")
    assert error.value.code == "MALFORMED_RESPONSE" and error.value.message


def test_worker_error_does_not_leak_exception_text(worker_script):
    worker = worker_script("import json\nprint(json.dumps({'protocol_version':1,'operation':'runtime.status','ok':False,'error':{'code':'STATUS_PROBE_FAILED','message':'/secret/admin_passwd=topsecret'}}))\n")
    with pytest.raises(BridgeError) as error:
        bridge(worker).invoke(operation="runtime.status", client="demo")
    assert error.value.code == "STATUS_PROBE_FAILED"
    assert "topsecret" not in error.value.message and "secret" not in error.value.message


@pytest.mark.parametrize("error_object", [
    {"code": "UNKNOWN", "message": "nope"},
    {"code": "STATUS_PROBE_FAILED"},
    {"code": "STATUS_PROBE_FAILED", "message": "nope", "extra": True},
    {"code": 7, "message": "nope"},
])
def test_worker_error_object_must_have_exact_keys_and_approved_code(worker_script, error_object):
    response = {"protocol_version": 1, "operation": "runtime.status", "ok": False, "error": error_object}
    with pytest.raises(BridgeError) as error:
        bridge(worker_script(f"import json\nprint(json.dumps({response!r}))\n")).invoke(operation="runtime.status", client="demo")
    assert error.value.code == "MALFORMED_RESPONSE"


def test_invalid_identifiers_are_rejected():
    with pytest.raises(BridgeError) as error:
        OdooTuiWorkerBridge(WorkerConfig(interpreter=Path(sys.executable), worker=Path(__file__))).invoke(operation="runtime.status", client="../demo")
    assert error.value.code == "INVALID_REQUEST"


def test_fixed_local_environment_and_exact_request_keys():
    request = OdooTuiWorkerBridge._request("clients.list", "", "local")
    assert set(request) == {"protocol_version", "operation", "client", "environment"}
    assert request == {"protocol_version": 1, "operation": "clients.list", "client": "", "environment": "local"}


def test_public_allowlist_has_exactly_seven_operations():
    import worker_bridge
    assert worker_bridge._ALLOWED_OPERATIONS == {"clients.list", "releases.list", "instance.identity", "runtime.status", "runtime.control", "modules.list", "databases.list"}
