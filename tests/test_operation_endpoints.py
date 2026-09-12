import json
from pathlib import Path

import pytest

import endpoints
import handlers
from adapters.odoo_tui_adapter import OdooTuiAdapter
from log_polling import PollingError
from operation_bridge import BridgeError


ROOT = Path(__file__).parents[1]
AUTH = {"authenticated": True, "authorized": True}


def current_precondition():
    snapshot = handlers.snapshot_collection()
    return {
        "registry_identity": snapshot["clients"][0]["registry_identity"],
        "process_instance_id": handlers._SNAPSHOT_PROCESS_INSTANCE_ID,
        "registry_epoch": snapshot["registry_epoch"],
    }


class ReadTransport:
    def request(self, operation, **params):
        files = {
            "clients.list": "clients.valid.json",
            "modules.list": "modules.valid.json",
        }
        return (ROOT / "tests" / "fixtures" / files[operation]).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def registered_read_adapter():
    handlers.register_read_adapter(OdooTuiAdapter(ReadTransport()))
    yield
    handlers.clear_read_adapter()


class Boundary:
    def __init__(self, data=None, error=None):
        self.calls = []
        self.data = data or {"state": "online", "control_mode": "client"}
        self.error = error

    def invoke(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return {"protocol_version": 1, "operation": request["operation"], "ok": True, "data": self.data}


def test_manifest_exposes_exact_read_and_mutation_routes():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert {(item["method"], item["path"]) for item in manifest["endpoints"]} == {
        ("GET", "/odoo-tui/clients"),
        ("GET", "/odoo-tui/releases"),
        ("GET", "/odoo-tui/snapshot"),
        ("GET", "/odoo-tui/instance/identity"),
        ("GET", "/odoo-tui/instance/status"),
        ("GET", "/odoo-tui/instance/control"),
        ("GET", "/odoo-tui/instance/modules"),
        ("GET", "/odoo-tui/instance/databases"),
        ("GET", "/odoo-tui/instance/logs"),
        ("POST", "/odoo-tui/instance/start"),
        ("POST", "/odoo-tui/instance/stop"),
        ("POST", "/odoo-tui/instance/restart"),
    }


def test_lifecycle_endpoint_sends_exact_confirmation_request():
    boundary = Boundary()
    handlers.register_operation_boundary(boundary)
    try:
        precondition = current_precondition()
        assert endpoints.startInstance({"confirmation": "START acme", "precondition": precondition}, {"client": "acme"}, AUTH) == boundary.data
        assert boundary.calls == [{
            "protocol_version": 1,
            "operation": "lifecycle.start",
            "client": "acme",
            "environment": "local",
            "confirmation": "START acme",
            "precondition": precondition,
        }]
    finally:
        handlers.clear_operation_boundary()




def test_lifecycle_endpoint_rejects_changed_selected_snapshot_before_dispatch():
    boundary = Boundary()
    handlers.register_operation_boundary(boundary)
    try:
        snapshot = handlers.snapshot_collection()
        precondition = {
            "registry_identity": snapshot["clients"][0]["registry_identity"] + "-changed",
            "process_instance_id": handlers._SNAPSHOT_PROCESS_INSTANCE_ID,
            "registry_epoch": snapshot["registry_epoch"],
        }
        with pytest.raises(PollingError) as error:
            endpoints.startInstance(
                {"confirmation": "START acme", "precondition": precondition},
                {"client": "acme"},
                AUTH,
            )
        assert (error.value.status_code, error.value.code) == (409, "PRECONDITION_FAILED")
        assert boundary.calls == []
    finally:
        handlers.clear_operation_boundary()


def test_start_endpoint_carries_selected_start_mode():
    boundary = Boundary()
    handlers.register_operation_boundary(boundary)
    try:
        endpoints.startInstance({"confirmation": "START acme", "mode": "database_manager", "precondition": current_precondition()}, {"client": "acme"}, AUTH)
        assert boundary.calls[0]["mode"] == "database_manager"
    finally:
        handlers.clear_operation_boundary()


def test_restart_endpoint_carries_selected_modules_without_confirmation_ui():
    boundary = Boundary()
    handlers.register_operation_boundary(boundary)
    try:
        endpoints.restartInstance(
            {"modules": ["parkair_account"], "confirmation": "RESTART acme", "precondition": current_precondition()},
            {"client": "acme"},
            AUTH,
        )
        assert boundary.calls[0] == {
            "protocol_version": 1,
            "operation": "lifecycle.restart",
            "client": "acme",
            "environment": "local",
            "confirmation": "RESTART acme",
            "precondition": boundary.calls[0]["precondition"],
            "modules": ["parkair_account"],
        }
    finally:
        handlers.clear_operation_boundary()


def test_restart_update_opens_log_window_only_while_worker_runs():
    boundary = Boundary()
    original_invoke = boundary.invoke

    def invoke(request):
        assert handlers.update_log_window_active("acme") is True
        return original_invoke(request)

    boundary.invoke = invoke
    handlers.register_operation_boundary(boundary)
    try:
        endpoints.restartInstance(
            {"modules": ["base"], "confirmation": "RESTART acme", "precondition": current_precondition()},
            {"client": "acme"},
            AUTH,
        )
        assert handlers.update_log_window_active("acme") is False
    finally:
        handlers.clear_operation_boundary()
        handlers.end_update_log_window("acme")


def test_failed_restart_update_retains_log_window_for_diagnosis():
    handlers.register_operation_boundary(Boundary(error=BridgeError("OPERATION_FAILED", "details")))
    try:
        with pytest.raises(PollingError):
            endpoints.restartInstance(
                {"modules": ["base"], "confirmation": "RESTART acme", "precondition": current_precondition()},
                {"client": "acme"},
                AUTH,
            )
        assert handlers.update_log_window_active("acme") is True
    finally:
        handlers.clear_operation_boundary()
        handlers.clear_log_sources()


def test_legacy_approval_body_and_extra_fields_are_rejected():
    boundary = Boundary()
    handlers.register_operation_boundary(boundary)
    try:
        with pytest.raises(PollingError) as error:
            endpoints.startInstance({"approved": True}, {"client": "acme"}, AUTH)
        assert (error.value.status_code, error.value.code) == (400, "INVALID_REQUEST")
        assert boundary.calls == []
    finally:
        handlers.clear_operation_boundary()


def test_update_all_plan_has_no_browser_module_or_database_input():
    boundary = Boundary(data={"kind": "update_all", "database": "19_acme", "modules": ["base"], "confirmation": "UPDATE ALL 19_acme"})
    handlers.register_operation_boundary(boundary)
    try:
        endpoints.planModuleUpdates({"update_all": True}, {"client": "acme"}, AUTH)
        assert boundary.calls[0] == {
            "protocol_version": 1,
            "operation": "updates.plan",
            "client": "acme",
            "environment": "local",
            "update_all": True,
        }
    finally:
        handlers.clear_operation_boundary()


def test_operation_in_progress_maps_to_http_409():
    handlers.register_operation_boundary(Boundary(error=BridgeError("OPERATION_IN_PROGRESS", "secret details")))
    try:
        with pytest.raises(PollingError) as error:
            endpoints.stopInstance({"confirmation": "STOP acme", "precondition": current_precondition()}, {"client": "acme"}, AUTH)
        assert (error.value.status_code, error.value.code) == (409, "OPERATION_IN_PROGRESS")
        assert "secret" not in str(error.value)
    finally:
        handlers.clear_operation_boundary()
