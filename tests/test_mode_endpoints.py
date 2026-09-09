import configparser
from concurrent.futures import ThreadPoolExecutor

import pytest

import endpoints
import handlers


class Config:
    def __init__(self, text="[options]\nmode = client\n"):
        self.text = text
        self.writes = 0

    def read(self):
        return self.text

    def write(self, text):
        self.writes += 1
        self.text = text


@pytest.fixture(autouse=True)
def reset_registry():
    handlers.clear_config_sources()
    yield
    handlers.clear_config_sources()


def auth(**extra):
    return {"authenticated": True, "authorized": True, **extra}


def test_client_mode_route_reads_config():
    config = Config()
    handlers.register_config_source("acme", config)

    result = endpoints.getMode({}, {"client": "acme"}, auth=auth())

    assert result == {"client": "acme", "mode": "client"}


def test_database_manager_mode_write_is_read_back_verified():
    config = Config()
    handlers.register_config_source("acme", config)

    result = endpoints.setMode({"mode": "database-manager"}, {"client": "acme"}, auth=auth())

    assert result == {"client": "acme", "mode": "database-manager", "verified": True}
    assert configparser.ConfigParser().read_string(config.text) is None


def test_database_manager_mode_route_reads_config():
    config = Config("[options]\nmode = database-manager\n")
    handlers.register_config_source("acme", config)

    result = endpoints.getMode({}, {"client": "acme"}, auth=auth())

    assert result == {"client": "acme", "mode": "database-manager"}


def test_client_mode_write_is_read_back_verified():
    config = Config("[options]\nmode = database-manager\n")
    handlers.register_config_source("acme", config)

    result = endpoints.setMode({"mode": "client"}, {"client": "acme"}, auth=auth())

    assert result == {"client": "acme", "mode": "client", "verified": True}
    assert config.writes == 1


def test_invalid_mode_does_not_write():
    config = Config()
    handlers.register_config_source("acme", config)

    with pytest.raises(handlers.ConfigurationError) as error:
        endpoints.setMode({"mode": "root"}, {"client": "acme"}, auth=auth())

    assert error.value.status_code == 400
    assert config.writes == 0


def test_malformed_request_does_not_write():
    config = Config()
    handlers.register_config_source("acme", config)

    with pytest.raises(handlers.ConfigurationError) as error:
        endpoints.setMode({"mode": "client", "unexpected": True}, {"client": "acme"}, auth=auth())

    assert error.value.status_code == 400
    assert config.writes == 0


def test_malformed_config_does_not_write():
    config = Config("[options\nmode = client\n")
    handlers.register_config_source("acme", config)

    with pytest.raises(handlers.ConfigurationError) as error:
        endpoints.setMode({"mode": "client"}, {"client": "acme"}, auth=auth())

    assert error.value.status_code == 422
    assert config.writes == 0


def test_unauthenticated_and_unauthorized_requests_are_rejected():
    config = Config()
    handlers.register_config_source("acme", config)

    with pytest.raises(handlers.ConfigurationError) as unauthenticated:
        endpoints.getMode({}, {"client": "acme"})
    with pytest.raises(handlers.ConfigurationError) as unauthorized:
        endpoints.setMode({"mode": "client"}, {"client": "acme"}, auth={"authenticated": True})

    assert unauthenticated.value.status_code == 401
    assert unauthorized.value.status_code == 403
    assert config.writes == 0


def test_missing_client_is_rejected():
    with pytest.raises(handlers.PollingError) as error:
        endpoints.getMode({}, {}, auth=auth())

    assert error.value.status_code == 400


def test_concurrent_mode_changes_are_serialized_and_read_back_verified():
    class ConcurrentConfig(Config):
        def get_admin_password(self):
            return None

        def read_text(self):
            return self.read()

        def write_text(self, text):
            self.write(text)

        def write(self, text):
            # Every completed write must be parseable; interleaved writes
            # would fail this assertion in the worker that observed one.
            parsed = configparser.ConfigParser()
            parsed.read_string(text)
            assert parsed.get("options", "mode") in {"client", "database-manager"}
            super().write(text)

    config = ConcurrentConfig()
    handlers.register_config_source("acme", config)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(endpoints.setMode, {"mode": mode}, {"client": "acme"}, auth=auth())
            for mode in ("client", "database-manager")
        ]
        results = [future.result() for future in futures]

    assert {result["mode"] for result in results} == {"client", "database-manager"}
    assert all(result["verified"] is True for result in results)
    assert endpoints.getMode({}, {"client": "acme"}, auth=auth())["mode"] in {
        "client",
        "database-manager",
    }
