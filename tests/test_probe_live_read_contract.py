import copy
import importlib.util
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "probe_live_read_contract",
    Path(__file__).parents[1] / "scripts" / "probe_live_read_contract.py",
)
probe = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(probe)


def valid_responses():
    return {
        "clients.list": {"clients": [{"name": "acme", "release": None, "environment": "local", "local_url": None}]},
        "releases.list": {"releases": [{"version": "18"}]},
        "instance.identity": {"instance": {"client": "acme", "release": "18", "environment": "local",
                                     "config_identity": None, "database": None, "http_port": 8069,
                                     "longpolling_port": None}},
        "runtime.status": {"status": {"state": "stopped", "pid": None, "process_name": None,
                                  "pm2_id": None, "startup_mode": None}},
        "modules.list": {"client": "acme", "modules": [{"name": "base", "version": None,
                                                        "installed": True, "installable": None,
                                                        "update_available": False, "dependencies": []}]},
        "databases.list": {"client": "acme", "databases": [{"name": "acme", "exists": True}]},
        "logs.list": {"entries": [{"timestamp": None, "pid": None, "level": None, "database": None, "logger": None, "message": "Odoo worker ready"}], "next_cursor": "cursor", "has_more": False, "cursor_reset": False},
    }


@pytest.mark.parametrize("operation, path, value", [
    ("clients.list", ("clients", 0, "name"), 4),
    ("releases.list", ("releases", 0, "version"), ""),
    ("instance.identity", ("instance", "http_port"), -1),
    ("runtime.status", ("status", "state"), "running"),
    ("modules.list", ("modules", 0, "dependencies"), ["", 4]),
    ("databases.list", ("databases", 0, "exists"), "yes"),
    ("logs.list", ("entries", 0, "message"), 4),
])
def test_each_route_rejects_malformed_nested_values(operation, path, value):
    response = copy.deepcopy(valid_responses()[operation])
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(SystemExit) as error:
        probe.validate(operation, response, "acme")
    assert error.value.code == 1


def test_instance_and_client_results_enforce_selector_relationship():
    response = copy.deepcopy(valid_responses()["instance.identity"])
    response["instance"]["client"] = "other"
    with pytest.raises(SystemExit):
        probe.validate("instance.identity", response, "acme")

    response = copy.deepcopy(valid_responses()["modules.list"])
    response["client"] = "other"
    with pytest.raises(SystemExit):
        probe.validate("modules.list", response, "acme")
