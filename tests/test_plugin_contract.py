import json
import importlib.util
from pathlib import Path

import endpoints
import handlers
import pytest
from adapters.odoo_tui_adapter import OdooTuiAdapter
from log_polling import PollingError


ROOT = Path(__file__).parents[1]


def test_backend_manifest_is_valid_and_matches_endpoint_exports():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "odoo-tui"
    assert manifest["version"] == "0.1.0"
    assert manifest["routePath"] == "/odoo-tui"
    assert manifest["navItem"]["to"] == manifest["routePath"]
    assert manifest["endpoints"]
    for endpoint in manifest["endpoints"]:
        assert endpoint["method"] in {"GET", "POST", "PUT", "DELETE"}
        assert endpoint["path"].startswith("/")
        assert endpoint["handler"]
        assert callable(getattr(endpoints, endpoint["handler"], None))
        assert endpoint["authRequired"] is True


def test_instance_routes_are_static_and_select_client_from_query():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    instance_routes = {
        (endpoint["method"], endpoint["path"])
        for endpoint in manifest["endpoints"]
        if endpoint["method"] == "GET" and "/instance/" in endpoint["path"]
    }

    assert instance_routes == {
        ("GET", "/odoo-tui/instance/identity"),
        ("GET", "/odoo-tui/instance/status"),
        ("GET", "/odoo-tui/instance/control"),
        ("GET", "/odoo-tui/instance/modules"),
        ("GET", "/odoo-tui/instance/databases"),
        ("GET", "/odoo-tui/instance/logs"),
    }
    assert all("{" not in path and "}" not in path for _, path in instance_routes)

    ui_manifest = (ROOT / "ui" / "manifest.ts").read_text(encoding="utf-8")
    assert "/odoo-tui/instances/" not in ui_manifest
    assert "{client}" not in ui_manifest


def test_host_loader_resolves_static_routes_and_rejects_missing_client(tmp_path):
    loader_path = Path("/home/cyclone/Developer/third_party/hermes-mission-control/server/plugins/loader.py")
    spec = importlib.util.spec_from_file_location("mission_control_plugin_loader", loader_path)
    assert spec is not None and spec.loader is not None
    loader_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader_module)

    external_dir = tmp_path / "plugins"
    external_dir.mkdir()
    (external_dir / "odoo-tui").symlink_to(ROOT, target_is_directory=True)
    loader = loader_module.PluginLoader(internal_dir=tmp_path / "empty", external_dir=external_dir)
    assert loader.load_plugin("odoo-tui") is True

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for endpoint in manifest["endpoints"]:
        handler = loader.resolve(endpoint["method"], endpoint["path"])
        assert handler is not None
        if "/instance/" not in endpoint["path"]:
            continue
        with pytest.raises((PollingError, handlers.ConfigurationError)) as error:
            handler.handler_fn({}, {}, {"authenticated": True, "authorized": True})
        assert error.value.status_code == 400
        assert error.value.code == "INVALID_REQUEST"

    class KnownClientTransport:
        def request(self, operation, **params):
            if operation == "clients.list":
                return {"clients": [{"name": "known", "release": "19", "environment": "local", "local_url": None}]}
            return {"instance": {"client": "known", "release": "19", "environment": "local", "config_identity": None,
                                  "database": None, "http_port": None, "longpolling_port": None}}

    handlers.register_read_adapter(OdooTuiAdapter(KnownClientTransport()))
    unknown_handler = loader.resolve("GET", "/odoo-tui/instance/identity")
    assert unknown_handler is not None
    with pytest.raises(PollingError) as error:
        unknown_handler.handler_fn({}, {"client": "unknown"}, {"authenticated": True})
    assert (error.value.status_code, error.value.code) == (404, "INSTANCE_NOT_FOUND")
    handlers.clear_read_adapter()


def test_log_endpoint_rejects_missing_authentication():
    with pytest.raises(PollingError) as error:
        endpoints.getLogs({}, {"client": "acme"})
    assert error.value.status_code == 401
    assert error.value.code == "UNAUTHENTICATED"