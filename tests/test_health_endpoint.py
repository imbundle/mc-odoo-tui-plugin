import pytest

import endpoints
from log_polling import PollingError


def test_health_returns_stable_authenticated_availability_response():
    auth = {"authenticated": True}

    first = endpoints.health({}, {}, auth=auth)
    second = endpoints.health({}, {}, auth=auth)

    assert first == {
        "ok": True,
        "available": True,
        "plugin": "odoo-tui",
    }
    assert second == first


def test_health_rejects_unauthenticated_or_malformed_authentication():
    for auth in (None, {}, {"authenticated": False}, {"authenticated": "yes"}):
        with pytest.raises(PollingError) as error:
            endpoints.health({}, {}, auth=auth)

        assert error.value.status_code == 401
        assert error.value.code == "UNAUTHENTICATED"


def test_health_rejects_nonempty_request_parts_without_touching_runtime():
    with pytest.raises(PollingError) as body_error:
        endpoints.health({"unexpected": True}, {}, auth={"authenticated": True})
    with pytest.raises(PollingError) as params_error:
        endpoints.health({}, {"unexpected": "true"}, auth={"authenticated": True})

    assert body_error.value.status_code == 400
    assert body_error.value.code == "INVALID_REQUEST"
    assert params_error.value.status_code == 400
    assert params_error.value.code == "INVALID_REQUEST"
