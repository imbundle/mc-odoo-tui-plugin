import json

import pytest

import endpoints
import handlers


class Config:
    def __init__(self, password):
        self.password = password
        self.reads = 0
        self.text = "[options]\nmode = client\n"

    def get_admin_password(self):
        self.reads += 1
        return self.password

    def read_text(self):
        return self.text

    def write_text(self, text):
        self.text = text


@pytest.fixture(autouse=True)
def reset_registry():
    handlers.clear_config_sources()
    yield
    handlers.clear_config_sources()


def authorized():
    return {"authenticated": True, "authorized": True}


def test_explicit_authenticated_request_reveals_configured_password():
    config = Config("s3cret")
    handlers.register_config_source("acme", config)

    result = endpoints.revealAdminPassword({}, {"client": "acme"}, auth=authorized())

    assert result == {"admin_passwd": "s3cret"}
    assert config.reads == 1


@pytest.mark.parametrize(
    ("auth", "status_code"),
    [(None, 401), ({"authenticated": False, "authorized": True}, 401), ({"authenticated": True}, 403)],
)
def test_password_reveal_requires_authentication_and_permission(auth, status_code):
    config = Config("s3cret")
    handlers.register_config_source("acme", config)

    with pytest.raises(handlers.PollingError) as error:
        endpoints.revealAdminPassword({}, {"client": "acme"}, auth=auth)

    assert error.value.status_code == status_code
    assert config.reads == 0
    assert "s3cret" not in str(error.value)


@pytest.mark.parametrize("password", [None, "", "   ", 123, {"secret": "s3cret"}])
def test_absent_or_malformed_password_is_unavailable_without_leaking_value(password):
    handlers.register_config_source("acme", Config(password))

    with pytest.raises(handlers.PollingError) as error:
        endpoints.revealAdminPassword({}, {"client": "acme"}, auth=authorized())

    assert error.value.status_code == 404
    assert error.value.code == "PASSWORD_UNAVAILABLE"
    assert "s3cret" not in str(error.value)


def test_malformed_config_exception_is_redacted():
    class BrokenConfig:
        def get_admin_password(self):
            raise ValueError("admin_passwd=s3cret")

        def read_text(self):
            return "[options]\nmode = client\n"

        def write_text(self, text):
            pass

    handlers.register_config_source("acme", BrokenConfig())

    with pytest.raises(handlers.PollingError) as error:
        endpoints.revealAdminPassword({}, {"client": "acme"}, auth=authorized())

    assert error.value.status_code == 404
    assert "s3cret" not in json.dumps({"code": error.value.code, "message": error.value.message})


def test_password_is_not_exposed_by_unrelated_mode_response():
    class ConfigWithPassword(Config):
        def read_text(self):
            return "[options]\nmode = client\nadmin_passwd = s3cret\n"

    handlers.register_config_source("acme", ConfigWithPassword("s3cret"))

    result = endpoints.getMode({}, {"client": "acme"}, auth=authorized())

    assert result == {"client": "acme", "mode": "client"}
    assert "s3cret" not in json.dumps(result)


def test_client_path_is_required_and_not_taken_from_untrusted_body():
    handlers.register_config_source("acme", Config("s3cret"))

    with pytest.raises(handlers.PollingError) as error:
        endpoints.revealAdminPassword({"client": "acme"}, {}, auth=authorized())

    assert error.value.status_code == 400