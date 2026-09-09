import json
from pathlib import Path

import pytest

import endpoints
import handlers
from adapters.odoo_tui_adapter import OdooTuiAdapter
from log_polling import PollingError


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTransport:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def request(self, operation, **params):
        self.calls.append((operation, params))
        return self.payloads[operation]


@pytest.fixture(autouse=True)
def reset_adapter():
    handlers.clear_read_adapter()
    yield
    handlers.clear_read_adapter()


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def register(payloads):
    payloads = {"clients.list": fixture("clients.valid.json"), **payloads}
    transport = FixtureTransport(payloads)
    handlers.register_read_adapter(OdooTuiAdapter(transport))
    return transport


def auth():
    return {"authenticated": True}


def test_module_and_database_endpoints_return_json_compatible_typed_results():
    transport = register({
        "modules.list": fixture("modules.valid.json"),
        "databases.list": fixture("databases.valid.json"),
    })

    modules = endpoints.getModules({}, {"client": "acme"}, auth())
    databases = endpoints.getDatabases({}, {"client": "acme"}, auth())

    assert modules == {
        "client": "acme",
        "modules": [
            {"name": "base", "version": "18.0.1.0", "installed": True, "installable": True, "update_available": False, "dependencies": []},
            {"name": "sale", "version": "18.0.1.0", "installed": True, "installable": True, "update_available": False, "dependencies": ["base"]},
        ],
    }
    assert databases == {
        "client": "acme",
        "databases": [{"name": "acme", "exists": True}, {"name": "acme_test", "exists": True}],
    }
    json.dumps(modules)
    json.dumps(databases)
    assert [operation for operation, _ in transport.calls] == [
        "clients.list", "modules.list", "clients.list", "databases.list"
    ]


def test_empty_registries_are_successful_empty_json_arrays():
    register({"modules.list": fixture("modules.empty.json"), "databases.list": fixture("databases.empty.json")})

    assert endpoints.getModules({}, {"client": "acme"}, auth()) == {"client": "acme", "modules": []}
    assert endpoints.getDatabases({}, {"client": "acme"}, auth()) == {"client": "acme", "databases": []}


def test_database_unavailability_and_malformed_module_payload_use_safe_adapter_path():
    register({"modules.list": fixture("malformed.json.txt"), "databases.list": fixture("databases.unavailable.json")})

    with pytest.raises(PollingError) as module_error:
        endpoints.getModules({}, {"client": "acme"}, auth())
    assert (module_error.value.status_code, module_error.value.code) == (503, "MALFORMED_RESPONSE")

    with pytest.raises(PollingError) as database_error:
        endpoints.getDatabases({}, {"client": "acme"}, auth())
    assert (database_error.value.status_code, database_error.value.code) == (503, "STATUS_PROBE_FAILED")
    assert "odoo-tui status probe failed" in database_error.value.message


def test_module_and_database_endpoints_require_authentication_and_do_not_write():
    transport = register({"modules.list": '{"modules": []}', "databases.list": '{"databases": []}'})

    for endpoint in (endpoints.getModules, endpoints.getDatabases):
        with pytest.raises(PollingError) as error:
            endpoint({}, {"client": "acme"})
        assert error.value.status_code == 401

    assert transport.calls == []
    assert not any(name in dir(OdooTuiAdapter) for name in ("create_database", "alter_database", "delete_database"))
