from pathlib import Path

import pytest

import endpoints
import handlers
from adapters.odoo_tui_adapter import OdooTuiAdapter
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
        }
        return (FIXTURES / names[operation]).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def registered_adapter():
    handlers.register_read_adapter(OdooTuiAdapter(FixtureTransport()))
    yield
    handlers.clear_read_adapter()


def test_read_endpoints_return_serializable_typed_models():
    assert endpoints.listClients({}, {}, AUTH) == {"clients": [
            {"name": "acme", "release": "18", "environment": "local", "local_url": "http://127.0.0.1:8069"},
            {"name": "demo", "release": "19", "environment": "local", "local_url": None},
        ]}
    assert endpoints.listReleases({}, {}, AUTH) == {"releases": [{"version": "18"}, {"version": "19"}]}
    assert endpoints.getInstanceIdentity({}, {"client": "acme"}, AUTH)["instance"]["http_port"] == 8069
    assert endpoints.getInstanceStatus({}, {"client": "acme"}, AUTH)["status"]["pid"] == 12345


def test_read_endpoints_require_authentication_and_client():
    with pytest.raises(PollingError) as auth_error:
        endpoints.listClients({}, {})
    assert (auth_error.value.status_code, auth_error.value.code) == (401, "UNAUTHENTICATED")

    with pytest.raises(PollingError) as client_error:
        endpoints.getInstanceStatus({}, {}, AUTH)
    assert (client_error.value.status_code, client_error.value.code) == (400, "INVALID_REQUEST")


def test_read_endpoints_reject_unknown_client_before_returning_empty_data():
    for endpoint in (endpoints.getInstanceIdentity, endpoints.getInstanceStatus, endpoints.getModules, endpoints.getDatabases):
        with pytest.raises(PollingError) as error:
            endpoint({}, {"client": "unknown"}, AUTH)
        assert (error.value.status_code, error.value.code) == (404, "INSTANCE_NOT_FOUND")


def test_unconfigured_adapter_is_a_safe_unavailable_error():
    handlers.clear_read_adapter()

    with pytest.raises(PollingError) as error:
        endpoints.listReleases({}, {}, AUTH)

    assert (error.value.status_code, error.value.code) == (503, "ADAPTER_UNAVAILABLE")
