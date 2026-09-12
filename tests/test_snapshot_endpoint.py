from pathlib import Path

import importlib.util
import pytest
import time

import endpoints
import handlers
from adapters.odoo_tui_adapter import OdooTuiAdapter
from adapters.odoo_tui_adapter import AdapterError
from worker_bridge import OdooTuiWorkerBridge, WorkerConfig, BridgeError
from log_polling import PollingError


FIXTURES = Path(__file__).parent / "fixtures"
AUTH = {"authenticated": True}


class FixtureTransport:
    def request(self, operation, **params):
        names = {
            "clients.list": "clients.valid.json",
            "releases.list": "releases.valid.json",
            "instance.identity": "instance.valid.json",
            "runtime.status": "status.valid.json",
            "runtime.control": "control.valid.json",
            "modules.list": "modules.valid.json",
            "databases.list": "databases.valid.json",
        }
        return (FIXTURES / names[operation]).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def registered_adapter():
    handlers.register_read_adapter(OdooTuiAdapter(FixtureTransport()))
    yield
    handlers.clear_read_adapter()


def test_snapshot_collection_has_coherent_registry_and_release_contract():
    result = endpoints.getSnapshot({}, {}, AUTH)

    assert set(result) == {
        "protocol_version",
        "process_instance_id",
        "registry_epoch",
        "registry_identity",
        "clients",
        "releases",
        "releases_error",
        "errors",
    }
    assert result["protocol_version"] == 1
    assert result["registry_epoch"] == 0
    assert [client["name"] for client in result["clients"]] == ["acme", "demo"]
    assert result["releases"] == [{"version": "18"}, {"version": "19"}]
    assert result["releases_error"] is None
    assert result["errors"] == {}
    assert "config_identity" not in result


def test_snapshot_selected_contains_all_capabilities_without_sensitive_identity_fields():
    result = endpoints.getSnapshot({}, {"client": "acme"}, AUTH)

    assert set(result) == {
        "protocol_version",
        "process_instance_id",
        "registry_epoch",
        "registry_identity",
        "releases",
        "releases_error",
        "client",
        "snapshot",
        "errors",
    }
    assert result["client"] == "acme"
    snapshot = result["snapshot"]
    assert set(snapshot) == {"registry_identity", "identity", "status", "control", "modules", "databases"}
    assert snapshot["identity"]["client"] == "acme"
    assert snapshot["modules"][0]["name"] == "base"
    assert snapshot["databases"][0]["name"] == "acme"
    assert "config_identity" not in snapshot["identity"]
    assert result["errors"] == {}


def test_snapshot_rejects_unauthenticated_and_unrepresentable_queries():
    with pytest.raises(Exception) as auth_error:
        endpoints.getSnapshot({}, {})
    assert (auth_error.value.status_code, auth_error.value.code) == (401, "UNAUTHENTICATED")

    with pytest.raises(Exception) as explicit_auth_error:
        endpoints.getSnapshot({}, {}, {"authenticated": False})
    assert (explicit_auth_error.value.status_code, explicit_auth_error.value.code) == (401, "UNAUTHENTICATED")

    trusted_result = endpoints.getSnapshot({}, {}, None)
    assert trusted_result["protocol_version"] == 1

    with pytest.raises(Exception) as query_error:
        endpoints.getSnapshot({}, {"unknown": ["value"]}, AUTH)
    assert (query_error.value.status_code, query_error.value.code) == (400, "INVALID_REQUEST")

    with pytest.raises(Exception) as duplicate_error:
        endpoints.getSnapshot({}, {"client": ["acme", "demo"]}, AUTH)
    assert (duplicate_error.value.status_code, duplicate_error.value.code) == (400, "INVALID_REQUEST")

    with pytest.raises(Exception) as prototype_error:
        endpoints.getSnapshot({}, {"client": "constructor"}, AUTH)
    assert (prototype_error.value.status_code, prototype_error.value.code) == (400, "INVALID_REQUEST")


def test_snapshot_isolates_a_failed_selected_capability():
    class FailingTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "modules.list":
                raise AdapterError("STATUS_PROBE_FAILED", "not exposed")
            return super().request(operation, **params)

    handlers.register_read_adapter(OdooTuiAdapter(FailingTransport()))
    result = endpoints.getSnapshot({}, {"client": "acme"}, AUTH)

    assert result["snapshot"]["modules"] is None
    assert result["errors"] == {"acme": {"modules": {"code": "MODULES_UNAVAILABLE"}}}
    assert result["snapshot"]["status"]["state"] == "online"


def test_snapshot_rejects_registry_overflow_without_truncation():
    class ManyClientsTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "clients.list":
                return {"clients": [
                    {"name": f"client_{index}", "release": "19", "environment": "local", "local_url": None}
                    for index in range(33)
                ]}
            return super().request(operation, **params)

    handlers.register_read_adapter(OdooTuiAdapter(ManyClientsTransport()))
    with pytest.raises(PollingError) as error:
        endpoints.getSnapshot({}, {}, AUTH)
    assert (error.value.status_code, error.value.code) == (503, "SNAPSHOT_TOO_LARGE")


def test_snapshot_times_out_without_waiting_for_slow_registry_worker():
    class SlowTransport(FixtureTransport):
        delayed = True

        def request(self, operation, **params):
            if operation == "clients.list" and self.delayed:
                self.delayed = False
                time.sleep(6)
            return super().request(operation, **params)

    handlers.register_read_adapter(OdooTuiAdapter(SlowTransport()))
    started = time.monotonic()
    with pytest.raises(PollingError) as error:
        endpoints.getSnapshot({}, {}, AUTH)
    elapsed = time.monotonic() - started
    assert (error.value.status_code, error.value.code) == (503, "SNAPSHOT_TIMEOUT")
    assert elapsed < 5.8
    time.sleep(1.2)
    assert endpoints.getSnapshot({}, {}, AUTH)["clients"]

def test_snapshot_rejects_registry_identity_release_mismatch():
    class WrongReleaseTransport(FixtureTransport):
        def request(self, operation, **params):
            payload = super().request(operation, **params)
            if operation == "instance.identity":
                return payload.replace('"release": "18"', '"release": "19"')
            return payload

    handlers.register_read_adapter(OdooTuiAdapter(WrongReleaseTransport()))
    result = endpoints.getSnapshot({}, {"client": "acme"}, AUTH)
    assert result["snapshot"]["identity"] is None
    assert result["errors"] == {"acme": {"identity": {"code": "MALFORMED_BACKEND_RESPONSE"}}}

def test_snapshot_rejects_registry_identity_environment_mismatch():
    class WrongEnvironmentTransport(FixtureTransport):
        def request(self, operation, **params):
            payload = super().request(operation, **params)
            if operation == "instance.identity":
                return payload.replace('"environment": "local"', '"environment": "remote"')
            return payload

    handlers.register_read_adapter(OdooTuiAdapter(WrongEnvironmentTransport()))
    result = endpoints.getSnapshot({}, {"client": "acme"}, AUTH)
    assert result["snapshot"]["identity"] is None
    assert result["errors"] == {"acme": {"identity": {"code": "MALFORMED_BACKEND_RESPONSE"}}}

def test_adapter_rejects_cross_client_provenance():
    class WrongClientTransport(FixtureTransport):
        def request(self, operation, **params):
            payload = super().request(operation, **params)
            if operation == "modules.list":
                return payload.replace('"client": "acme"', '"client": "demo"')
            return payload

    adapter = OdooTuiAdapter(WrongClientTransport())
    with pytest.raises(AdapterError) as error:
        adapter.list_modules("acme")
    assert error.value.code == "MALFORMED_RESPONSE"


def test_legacy_identity_provenance_keeps_stale_identity_code():
    class WrongIdentityTransport(FixtureTransport):
        def request(self, operation, **params):
            payload = super().request(operation, **params)
            if operation == "instance.identity":
                return payload.replace('"client": "acme"', '"client": "demo"')
            return payload

    adapter = OdooTuiAdapter(WrongIdentityTransport())
    with pytest.raises(AdapterError) as error:
        adapter.get_instance("acme")
    assert error.value.code == "STALE_INSTANCE_IDENTITY"


def test_worker_bridge_reports_transport_output_overflow(tmp_path):
    worker = tmp_path / "overflow_worker.py"
    worker.write_text("print('x' * 70000)\n", encoding="utf-8")
    bridge = OdooTuiWorkerBridge(WorkerConfig(interpreter=Path(__import__('sys').executable), worker=worker, timeout_seconds=1.0))

    with pytest.raises(BridgeError) as error:
        bridge.invoke(operation="runtime.status", client="acme")

    assert error.value.code == "OUTPUT_TOO_LARGE"


def test_legacy_adapter_preserves_malformed_response_for_transport_overflow():
    class OverflowTransport:
        def request(self, operation, **params):
            raise BridgeError("OUTPUT_TOO_LARGE", "too large")

    adapter = OdooTuiAdapter(OverflowTransport())
    with pytest.raises(AdapterError) as error:
        adapter.get_status("acme")
    assert error.value.code == "MALFORMED_RESPONSE"
    assert getattr(error.value, "transport_code", None) == "OUTPUT_TOO_LARGE"


def test_snapshot_reaches_the_real_mission_control_plugin_dispatcher(tmp_path):
    host_loader_path = Path("/home/cyclone/Developer/third_party/hermes-mission-control/server/plugins/loader.py")
    spec = importlib.util.spec_from_file_location("snapshot_dispatch_loader", host_loader_path)
    assert spec is not None and spec.loader is not None
    host_loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host_loader)
    external_dir = tmp_path / "plugins"
    external_dir.mkdir()
    (external_dir / "odoo-tui").symlink_to(Path(__file__).parents[1], target_is_directory=True)
    loader = host_loader.PluginLoader(internal_dir=tmp_path / "empty", external_dir=external_dir)
    assert loader.load_plugin("odoo-tui") is True
    host_loader._loader = loader
    plugin_module = loader.get_module("odoo-tui")
    assert plugin_module is not None
    plugin_module.handlers.register_read_adapter(plugin_module.handlers.OdooTuiAdapter(FixtureTransport()))
    try:
        handled, response, status = host_loader.dispatch_plugin_request(
            "GET", "/api/local/odoo-tui/snapshot", {}, {}, None
        )
        selected_handled, selected_response, selected_status = host_loader.dispatch_plugin_request(
            "GET", "/api/local/odoo-tui/snapshot", {}, {"client": ["acme"]}, None
        )
        rejected_handled, rejected_response, rejected_status = host_loader.dispatch_plugin_request(
            "GET", "/api/local/odoo-tui/snapshot", {}, {}, {"authenticated": False}
        )
    finally:
        plugin_module.handlers.clear_read_adapter()
        host_loader._loader = None

    assert handled is True
    assert status == 200
    assert response["protocol_version"] == 1
    assert [client["name"] for client in response["clients"]] == ["acme", "demo"]
    assert selected_handled is True
    assert selected_status == 200
    assert selected_response["client"] == "acme"
    assert rejected_handled is True
    assert rejected_status == 401
    assert rejected_response["error"] == "UNAUTHENTICATED"


def test_snapshot_normalizes_missing_adapter():
    handlers.clear_read_adapter()
    with pytest.raises(PollingError) as error:
        endpoints.getSnapshot({}, {}, AUTH)
    assert (error.value.status_code, error.value.code) == (503, "REGISTRY_UNAVAILABLE")
