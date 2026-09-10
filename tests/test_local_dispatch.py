import importlib.util
import json
from pathlib import Path

import pytest

import handlers
from adapters.odoo_tui_adapter import OdooTuiAdapter


ROOT = Path(__file__).parents[1]
HOST_LOADER = Path("/home/cyclone/Developer/third_party/hermes-mission-control/server/plugins/loader.py")
FIXTURES = Path(__file__).parent / "fixtures"
AUTH = {"authenticated": True}


class FixtureTransport:
    def request(self, operation, **params):
        names = {
            "clients.list": "clients.valid.json",
            "releases.list": "releases.valid.json",
            "instance.identity": "instance.valid.json",
            "runtime.status": "status.valid.json",
            "modules.list": "modules.valid.json",
            "databases.list": "databases.valid.json",
        }
        return (FIXTURES / names[operation]).read_text(encoding="utf-8")


@pytest.fixture()
def dispatch_loader(tmp_path):
    spec = importlib.util.spec_from_file_location("mission_control_dispatch_loader", HOST_LOADER)
    assert spec is not None and spec.loader is not None
    loader_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader_module)

    external_dir = tmp_path / "plugins"
    external_dir.mkdir()
    (external_dir / "odoo-tui").symlink_to(ROOT, target_is_directory=True)
    loader = loader_module.PluginLoader(internal_dir=tmp_path / "empty", external_dir=external_dir)
    assert loader.load_plugin("odoo-tui") is True
    loader_module._loader = loader
    plugin_module = loader.get_module("odoo-tui")
    assert plugin_module is not None
    loader_module._odoo_handlers = plugin_module.handlers
    loader_module._odoo_handlers.register_read_adapter(loader_module._odoo_handlers.OdooTuiAdapter(FixtureTransport()))
    yield loader_module
    loader_module._odoo_handlers.clear_read_adapter()
    loader_module._loader = None


@pytest.mark.parametrize(
    ("path", "params", "key"),
    [
        ("/odoo-tui/clients", {}, "clients"),
        ("/odoo-tui/releases", {}, "releases"),
        ("/odoo-tui/instance/identity", {"client": ["acme"]}, "instance"),
        ("/odoo-tui/instance/status", {"client": ["acme"]}, "status"),
        ("/odoo-tui/instance/modules", {"client": ["acme"]}, "modules"),
        ("/odoo-tui/instance/databases", {"client": ["acme"]}, "databases"),
    ],
)
def test_authenticated_local_dispatch_reaches_every_read_group(dispatch_loader, path, params, key):
    handled, response, status = dispatch_loader.dispatch_plugin_request(
        "GET", f"/api/local{path}", {}, params, AUTH
    )

    assert handled is True
    assert status == 200
    assert key in response


@pytest.mark.parametrize(
    "path",
    [
        "/odoo-tui/clients",
        "/odoo-tui/releases",
        "/odoo-tui/instance/identity",
        "/odoo-tui/instance/status",
        "/odoo-tui/instance/modules",
        "/odoo-tui/instance/databases",
    ],
)
def test_unauthenticated_local_dispatch_is_rejected(dispatch_loader, path):
    params = {"client": ["acme"]} if "/instance/" in path else {}

    handled, response, status = dispatch_loader.dispatch_plugin_request(
        "GET", f"/api/local{path}", {}, params, {"authenticated": False}
    )

    assert handled is True
    assert status == 401
    assert response == {
        "error": "UNAUTHENTICATED",
        "detail": "Authentication is required.",
    }


def test_local_dispatch_maps_malformed_stale_and_unavailable_read_errors(dispatch_loader):
    class ErrorTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "instance.identity":
                return (FIXTURES / "instance.stale.json").read_text(encoding="utf-8")
            if operation == "runtime.status":
                return (FIXTURES / "status.unavailable.json").read_text(encoding="utf-8")
            if operation == "modules.list":
                return (FIXTURES / "malformed.json.txt").read_text(encoding="utf-8")
            return super().request(operation, **params)

    dispatch_loader._odoo_handlers.register_read_adapter(dispatch_loader._odoo_handlers.OdooTuiAdapter(ErrorTransport()))
    checks = [
        ("/odoo-tui/instance/identity", "STALE_INSTANCE_IDENTITY", 404),
        ("/odoo-tui/instance/status", "STATUS_PROBE_FAILED", 503),
        ("/odoo-tui/instance/modules", "MALFORMED_RESPONSE", 503),
    ]
    for path, code, expected_status in checks:
        handled, response, status = dispatch_loader.dispatch_plugin_request(
            "GET", f"/api/local{path}", {}, {"client": ["acme"]}, AUTH
        )
        assert handled is True
        assert status == expected_status
        assert response["error"] == code


def test_client_registry_failure_is_translated_through_dispatch(dispatch_loader):
    class UnavailableTransport(FixtureTransport):
        def request(self, operation, **params):
            if operation == "clients.list":
                return (FIXTURES / "databases.unavailable.json").read_text(encoding="utf-8")
            return super().request(operation, **params)

    dispatch_loader._odoo_handlers.register_read_adapter(dispatch_loader._odoo_handlers.OdooTuiAdapter(UnavailableTransport()))
    handled, response, status = dispatch_loader.dispatch_plugin_request(
        "GET", "/api/local/odoo-tui/instance/status", {}, {"client": ["acme"]}, AUTH
    )

    assert handled is True
    assert status == 503
    assert response["error"] == "STATUS_PROBE_FAILED"


def test_local_manifest_exposes_only_read_operations_for_six_groups():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    read_paths = {
        "/odoo-tui/clients",
        "/odoo-tui/releases",
        "/odoo-tui/instance/identity",
        "/odoo-tui/instance/status",
        "/odoo-tui/instance/modules",
        "/odoo-tui/instance/databases",
    }
    assert {
        endpoint["path"] for endpoint in manifest["endpoints"]
        if endpoint["path"] in read_paths
    } == read_paths
    assert all(endpoint["method"] == "GET" for endpoint in manifest["endpoints"] if endpoint["path"] in read_paths)
    assert not any(endpoint["path"] in read_paths and endpoint["method"] != "GET" for endpoint in manifest["endpoints"])


def test_endpoint_module_registers_production_adapter_on_load():
    import importlib
    import endpoints

    handlers.clear_read_adapter()
    importlib.reload(endpoints)
    try:
        assert isinstance(handlers._read_adapter, OdooTuiAdapter)
    finally:
        handlers.clear_read_adapter()


def test_unknown_and_stale_instance_error_mapping(dispatch_loader):
    class Transport:
        def request(self, operation, **params):
            if operation == "clients.list":
                return {"clients": [{"name": "known", "release": "19", "environment": "local", "local_url": None}]}
            return {"instance": {"client": "other", "release": "19", "environment": "local", "config_identity": None,
                                  "database": None, "http_port": None, "longpolling_port": None}}
    dispatch_loader._odoo_handlers.register_read_adapter(dispatch_loader._odoo_handlers.OdooTuiAdapter(Transport()))
    _, unknown, unknown_status = dispatch_loader.dispatch_plugin_request("GET", "/api/local/odoo-tui/instance/identity", {}, {"client": ["missing"]}, AUTH)
    assert unknown_status == 404 and unknown["error"] == "INSTANCE_NOT_FOUND"
    _, stale, stale_status = dispatch_loader.dispatch_plugin_request("GET", "/api/local/odoo-tui/instance/identity", {}, {"client": ["known"]}, AUTH)
    assert stale_status == 404 and stale["error"] == "STALE_INSTANCE_IDENTITY"


@pytest.mark.parametrize("params", [{}, {"client": []}, {"client": ["acme", "demo"]}, {"client": ["../acme"]}, {"client": [""]}])
def test_malformed_client_selectors_map_to_invalid_request(dispatch_loader, params):
    _, response, status = dispatch_loader.dispatch_plugin_request(
        "GET", "/api/local/odoo-tui/instance/status", {}, params, AUTH
    )
    assert status == 400
    assert response == {"error": "INVALID_REQUEST", "detail": "client must be a valid selector."} or response == {
        "error": "INVALID_REQUEST", "detail": "client must be specified exactly once."
    }
