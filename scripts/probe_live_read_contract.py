#!/usr/bin/env python3
"""Stdlib, redacted live read-contract probe for telemetry port 6001."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:6001/api/local/odoo-tui"
ENV = Path("/home/cyclone/.hermes/mission-control-linux.env")
CLIENT_RE = re.compile(r"[a-z0-9][a-z0-9_]*\Z")
OPS = [
    ("/clients", "clients.list", "clients"),
    ("/releases", "releases.list", "releases"),
    ("/instance/identity", "instance.identity", "instance"),
    ("/instance/status", "runtime.status", "status"),
    ("/instance/control", "runtime.control", "control"),
    ("/instance/modules", "modules.list", "modules"),
    ("/instance/databases", "databases.list", "databases"),
    ("/instance/logs", "logs.list", "logs"),
]


def fail(code: str) -> None:
    print(f"FAIL {code}")
    raise SystemExit(1)


def token() -> str:
    try:
        text = ENV.read_text(encoding="utf-8")
    except OSError:
        fail("BLOCKED_NO_TOKEN")
    match = re.search(r"(?m)^\s*MISSION_CONTROL_TOKEN\s*=\s*(.*?)\s*$", text)
    if not match:
        fail("BLOCKED_NO_TOKEN")
    value = match.group(1).strip().strip('"\'')
    if not value:
        fail("BLOCKED_NO_TOKEN")
    return value


def get(url: str, auth: str | None) -> tuple[int, object]:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        try:
            body = json.loads(error.read())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            body = None
        return error.code, body
    except (OSError, URLError, json.JSONDecodeError):
        fail("REQUEST_EXCEPTION")


def strict_obj(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def non_negative_int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def string_array(value: object) -> bool:
    return isinstance(value, list) and all(non_empty_string(item) for item in value)


def validate_client(value: object) -> bool:
    return (
        strict_obj(value, {"name", "release", "environment", "local_url"})
        and isinstance(value["name"], str)
        and bool(value["name"].strip())
        and re.fullmatch(CLIENT_RE, value["name"]) is not None
        and all(value[key] is None or non_empty_string(value[key])
                for key in ("release", "environment", "local_url"))
    )


def validate_release(value: object) -> bool:
    return strict_obj(value, {"version"}) and non_empty_string(value["version"])


def validate_identity(value: object, client: str) -> bool:
    return (
        strict_obj(value, {"client", "release", "environment", "config_identity", "database", "http_port", "longpolling_port"})
        and non_empty_string(value["client"])
        and value["client"] == client
        and non_empty_string(value["release"])
        and non_empty_string(value["environment"])
        and all(value[key] is None or non_empty_string(value[key]) for key in ("config_identity", "database"))
        and non_negative_int_or_none(value["http_port"])
        and non_negative_int_or_none(value["longpolling_port"])
    )


def validate_status(value: object) -> bool:
    return (
        strict_obj(value, {"state", "pid", "process_name", "pm2_id", "startup_mode"})
        and value["state"] in {"online", "stopped", "errored", "unknown"}
        and non_negative_int_or_none(value["pid"])
        and (value["process_name"] is None or non_empty_string(value["process_name"]))
        and non_negative_int_or_none(value["pm2_id"])
        and (value["startup_mode"] is None or non_empty_string(value["startup_mode"]))
    )


def validate_module(value: object) -> bool:
    return (
        strict_obj(value, {"name", "version", "installed", "installable", "update_available", "dependencies"})
        and non_empty_string(value["name"])
        and (value["version"] is None or non_empty_string(value["version"]))
        and isinstance(value["installed"], bool)
        and (value["installable"] is None or isinstance(value["installable"], bool))
        and isinstance(value["update_available"], bool)
        and string_array(value["dependencies"])
    )


def validate_database(value: object) -> bool:
    return strict_obj(value, {"name", "exists"}) and non_empty_string(value["name"]) and isinstance(value["exists"], bool)


def validate_log_entry(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        strict_obj(value, {"timestamp", "pid", "level", "database", "logger", "message"})
        and (value["timestamp"] is None or non_empty_string(value["timestamp"]))
        and non_negative_int_or_none(value["pid"])
        and (value["level"] is None or value["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
        and (value["database"] is None or non_empty_string(value["database"]))
        and (value["logger"] is None or non_empty_string(value["logger"]))
        and isinstance(value["message"], str)
    )


def validate(op: str, data: object, client: str | None = None) -> None:
    valid = False
    if op == "clients.list":
        valid = strict_obj(data, {"clients"}) and isinstance(data["clients"], list) and all(validate_client(item) for item in data["clients"])
    elif op == "releases.list":
        valid = strict_obj(data, {"releases"}) and isinstance(data["releases"], list) and all(validate_release(item) for item in data["releases"])
    elif op == "instance.identity":
        valid = isinstance(client, str) and strict_obj(data, {"instance"}) and validate_identity(data["instance"], client)
    elif op == "runtime.status":
        valid = strict_obj(data, {"status"}) and validate_status(data["status"])
    elif op == "runtime.control":
        if isinstance(data, dict) and strict_obj(data, {"control"}):
            control = data["control"]
            reasons = {
                "client": None,
                "database_manager": "Database Manager mode is not controllable here",
                "unknown": "Runtime identity could not be verified",
            }
            valid = (
                strict_obj(control, {"control_mode", "lifecycle_eligible", "reason"})
                and control["control_mode"] in reasons
                and isinstance(control["lifecycle_eligible"], bool)
                and control["lifecycle_eligible"] is (control["control_mode"] == "client")
                and control["reason"] == reasons[control["control_mode"]]
            )
    elif op == "modules.list":
        valid = isinstance(client, str) and strict_obj(data, {"client", "modules"}) and data["client"] == client and isinstance(data["modules"], list) and all(validate_module(item) for item in data["modules"])
    elif op == "databases.list":
        valid = isinstance(client, str) and strict_obj(data, {"client", "databases"}) and data["client"] == client and isinstance(data["databases"], list) and all(validate_database(item) for item in data["databases"])
    elif op == "logs.list":
        if isinstance(data, dict):
            valid = (
                isinstance(client, str)
                and strict_obj(data, {"entries", "next_cursor", "has_more", "cursor_reset"})
                and isinstance(data["entries"], list)
                and all(validate_log_entry(item) for item in data["entries"])
                and (data["next_cursor"] is None or non_empty_string(data["next_cursor"]))
                and isinstance(data["has_more"], bool)
                and isinstance(data["cursor_reset"], bool)
            )
    if not valid:
        fail("SCHEMA_MISMATCH")


def main() -> int:
    auth = token()
    status, body = get(f"{BASE}/clients", None)
    if status != 401:
        fail("UNAUTHENTICATED_STATUS")
    status, body = get(f"{BASE}/clients", auth)
    if status != 200:
        fail("AUTHENTICATED_STATUS")
    validate("clients.list", body)
    clients = body["clients"] if isinstance(body, dict) else None
    if not clients or not isinstance(clients[0], dict) or not isinstance(clients[0].get("name"), str) or CLIENT_RE.fullmatch(clients[0]["name"]) is None:
        fail("BLOCKED_NO_CLIENT")
    selected_client = clients[0]["name"]
    encoded_client = quote(selected_client, safe="")
    for ordinal, (path, operation, _) in enumerate(OPS, 1):
        suffix = f"?client={encoded_client}" if operation not in {"clients.list", "releases.list"} else ""
        url = BASE + path + suffix
        status, _ = get(url, None)
        if status != 401:
            fail(f"ROUTE_{ordinal}_UNAUTHENTICATED")
        status, response = get(url, auth)
        if status != 200:
            fail(f"ROUTE_{ordinal}_AUTHENTICATED")
        validate(operation, response, selected_client)
        print(f"{ordinal} HTTP {status} {operation} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
