from pathlib import Path

import endpoints
import handlers
import json
import pytest
from odoo_tui_worker import _ALLOWED_OPERATIONS as WORKER_ALLOWED_OPERATIONS
from worker_bridge import _ALLOWED_OPERATIONS as BRIDGE_ALLOWED_OPERATIONS
from adapters.odoo_tui_adapter import OdooTuiAdapter


FIXTURES = Path(__file__).parent / "fixtures"
AUTH = {"authenticated": True}


class FixtureTransport:
    def request(self, operation, **params):
        if operation == "clients.list":
            return (FIXTURES / "clients.valid.json").read_text(encoding="utf-8")
        assert operation == "runtime.control"
        assert params == {"client": "acme"}
        return (FIXTURES / "control.valid.json").read_text(encoding="utf-8")


def test_instance_control_returns_strict_control_contract():
    handlers.register_read_adapter(OdooTuiAdapter(FixtureTransport()))
    try:
        assert endpoints.getInstanceControl({}, {"client": "acme"}, AUTH) == {
            "control": {
                "control_mode": "client",
                "lifecycle_eligible": True,
                "reason": None,
            }
        }
    finally:
        handlers.clear_read_adapter()


def test_control_route_is_registered_in_manifest_and_read_allowlists():
    manifest = json.loads((Path(__file__).parents[1] / "manifest.json").read_text(encoding="utf-8"))
    route = next(item for item in manifest["endpoints"] if item["handler"] == "getInstanceControl")
    assert route == {
        "method": "GET",
        "path": "/odoo-tui/instance/control",
        "handler": "getInstanceControl",
        "authRequired": True,
    }
    assert "runtime.control" in WORKER_ALLOWED_OPERATIONS
    assert "runtime.control" in BRIDGE_ALLOWED_OPERATIONS


def test_control_schema_rejects_extra_fields():
    class InvalidTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "runtime.control":
                return '{"control":{"control_mode":"client","lifecycle_eligible":true,"reason":null,"extra":true}}'
            return super().request(operation, **params)

    handlers.register_read_adapter(OdooTuiAdapter(InvalidTransport()))
    try:
        with pytest.raises(Exception) as error:
            endpoints.getInstanceControl({}, {"client": "acme"}, AUTH)
        assert getattr(error.value, "code", None) == "MALFORMED_RESPONSE"
    finally:
        handlers.clear_read_adapter()


def test_control_probe_failure_is_safe_and_redacted():
    class UnavailableTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "runtime.control":
                return (FIXTURES / "control.unavailable.json").read_text(encoding="utf-8")
            return super().request(operation, **params)

    handlers.register_read_adapter(OdooTuiAdapter(UnavailableTransport()))
    try:
        with pytest.raises(Exception) as error:
            endpoints.getInstanceControl({}, {"client": "acme"}, AUTH)
        assert getattr(error.value, "code", None) == "STATUS_PROBE_FAILED"
        assert "internal PM2" not in str(error.value)
    finally:
        handlers.clear_read_adapter()
