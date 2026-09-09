from pathlib import Path

import pytest

from adapters.odoo_tui_adapter import AdapterError, OdooTuiAdapter
from worker_bridge import WorkerConfig


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTransport:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def request(self, operation, **params):
        self.calls.append((operation, params))
        return self.payloads[operation]


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def adapter_for(**payloads):
    return OdooTuiAdapter(FixtureTransport(payloads))


def test_valid_read_models_are_typed_and_stably_sorted():
    transport = FixtureTransport(
        {
            "clients.list": fixture("clients.valid.json"),
            "releases.list": fixture("releases.valid.json"),
            "instance.identity": fixture("instance.valid.json"),
            "runtime.status": fixture("status.valid.json"),
            "modules.list": fixture("modules.valid.json"),
            "databases.list": fixture("databases.valid.json"),
        }
    )
    adapter = OdooTuiAdapter(transport)

    assert [item.name for item in adapter.list_clients()] == ["acme", "demo"]
    assert [item.version for item in adapter.list_releases()] == ["18", "19"]
    assert adapter.get_instance("acme").http_port == 8069
    assert adapter.get_status("acme").pid == 12345
    assert [item.name for item in adapter.list_modules("acme")] == ["base", "sale"]
    assert [item.name for item in adapter.list_databases("acme")] == ["acme", "acme_test"]
    assert [operation for operation, _ in transport.calls] == [
        "clients.list",
        "releases.list",
        "instance.identity",
        "runtime.status",
        "modules.list",
        "databases.list",
    ]


def test_empty_registries_are_valid_empty_typed_results():
    empty = fixture("registries.empty.json")
    adapter = adapter_for(
        **{
            "clients.list": fixture("clients.empty.json"),
            "releases.list": fixture("releases.empty.json"),
            "modules.list": fixture("modules.empty.json"),
            "databases.list": fixture("databases.empty.json"),
        }
    )

    assert adapter.list_clients() == ()
    assert adapter.list_releases() == ()
    assert adapter.list_modules("acme") == ()
    assert adapter.list_databases("acme") == ()


def test_malformed_json_fails_with_actionable_operation_context():
    adapter = adapter_for(**{"clients.list": fixture("malformed.json.txt")})

    with pytest.raises(AdapterError, match="clients.list response was not valid JSON") as error:
        adapter.list_clients()

    assert error.value.code == "MALFORMED_RESPONSE"
    assert error.value.status_code == 503


def test_stale_identity_is_a_not_found_adapter_error():
    adapter = adapter_for(**{"instance.identity": fixture("instance.stale.json")})

    with pytest.raises(AdapterError) as error:
        adapter.get_instance("acme")

    assert error.value.code == "STALE_INSTANCE_IDENTITY"
    assert error.value.status_code == 404


def test_unavailable_pm2_and_postgres_data_are_not_silent_empty_results():
    adapter = adapter_for(
        **{
            "runtime.status": fixture("status.unavailable.json"),
            "databases.list": fixture("databases.unavailable.json"),
        }
    )

    with pytest.raises(AdapterError, match="odoo-tui status probe failed") as status_error:
        adapter.get_status("acme")
    assert status_error.value.code == "STATUS_PROBE_FAILED"

    with pytest.raises(AdapterError, match="odoo-tui status probe failed") as database_error:
        adapter.list_databases("acme")
    assert database_error.value.code == "STATUS_PROBE_FAILED"


def test_transport_failures_are_redacted_and_mapped_to_safe_errors():
    class UnavailableTransport:
        def request(self, operation, **params):
            raise RuntimeError("secret path /etc/odoo.conf and password=hidden")

    adapter = OdooTuiAdapter(UnavailableTransport())

    with pytest.raises(AdapterError) as error:
        adapter.list_clients()

    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
    assert "secret" not in error.value.message
    assert "password" not in error.value.message
    assert error.value.message == "The clients.list read is unavailable."


def test_adapter_exposes_only_read_operations():
    transport = FixtureTransport({"clients.list": '{"clients": []}'})
    adapter = OdooTuiAdapter(transport)

    adapter.list_clients()

    assert all(
        operation.endswith((".list", ".identity", ".status"))
        for operation, _ in transport.calls
    )


def test_adapter_translates_worker_bridge_status_fixture(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'protocol_version': 1, 'operation': request['operation'], 'ok': True, 'data': {'status': {'state': 'online', 'pid': 42, 'process_name': 'odoo-demo-local', 'pm2_id': None, 'startup_mode': None}}}))\n",
        encoding="utf-8",
    )
    adapter = OdooTuiAdapter(
        worker_config=WorkerConfig(
            interpreter=__import__("sys").executable,
            worker=worker,
        )
    )

    assert adapter.get_status("demo").__dict__ == {
        "state": "online",
        "pid": 42,
        "process_name": "odoo-demo-local",
        "pm2_id": None,
        "startup_mode": None,
    }


def test_adapter_status_probe_uses_only_read_only_runtime_status():
    transport = FixtureTransport({"runtime.status": '{"status": {"state": "stopped", "pid": null, "process_name": null, "pm2_id": null, "startup_mode": null}}'})
    adapter = OdooTuiAdapter(transport)

    assert adapter.get_status("demo").state == "stopped"
    assert transport.calls == [("runtime.status", {"client": "demo"})]
    assert all(operation == "runtime.status" for operation, _ in transport.calls)


def test_adapter_preserves_deterministic_bridge_failures(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
    adapter = OdooTuiAdapter(
        worker_config=WorkerConfig(
            interpreter=__import__("sys").executable,
            worker=worker,
            timeout_seconds=0.01,
        )
    )

    with pytest.raises(AdapterError) as error:
        adapter.get_status("demo")

    assert error.value.code == "TIMEOUT"
    assert error.value.message == "odoo-tui worker timed out"


def test_adapter_rejects_mixed_production_and_test_transport_configuration():
    with pytest.raises(ValueError, match="mutually exclusive"):
        OdooTuiAdapter(FixtureTransport({}), worker_config=WorkerConfig())


@pytest.mark.parametrize("code", ["INVALID_REQUEST", "OPERATION_NOT_ALLOWED"])
def test_adapter_maps_request_bridge_errors_to_http_bad_request(code):
    from worker_bridge import BridgeError

    class InvalidTransport:
        def request(self, operation, **params):
            raise BridgeError(code, "invalid request")

    with pytest.raises(AdapterError) as error:
        OdooTuiAdapter(InvalidTransport()).list_clients()
    assert (error.value.status_code, error.value.code) == (400, code)


def test_missing_and_extra_nested_fields_are_malformed():
    payload = {"client": "acme", "modules": [{"name": "base", "version": None, "installed": True, "installable": None,
                              "update_available": False, "dependencies": [], "unexpected": True}]}
    with pytest.raises(AdapterError) as error:
        OdooTuiAdapter(FixtureTransport({"modules.list": payload})).list_modules("acme")
    assert error.value.code == "MALFORMED_RESPONSE"


@pytest.mark.parametrize("payload", [
    {"error": {"code": "STATUS_PROBE_FAILED", "message": "safe"}, "extra": True},
    {"error": {"code": "STATUS_PROBE_FAILED", "message": "safe", "extra": True}},
])
def test_error_envelope_rejects_extra_fields(payload):
    with pytest.raises(AdapterError) as error:
        OdooTuiAdapter(FixtureTransport({"clients.list": payload})).list_clients()
    assert error.value.code == "MALFORMED_RESPONSE"


@pytest.mark.parametrize("modules", [None, "not-a-list", 7])
def test_null_and_scalar_modules_fields_are_malformed(modules):
    payload = {"client": "acme", "modules": modules}
    with pytest.raises(AdapterError) as error:
        OdooTuiAdapter(FixtureTransport({"modules.list": payload})).list_modules("acme")
    assert error.value.code == "MALFORMED_RESPONSE"
