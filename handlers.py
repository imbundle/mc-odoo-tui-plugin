"""Plugin-owned log source registry and polling service boundary."""
from __future__ import annotations

import threading
import time
import re
from os import PathLike
from pathlib import Path
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from adapters.odoo_tui_adapter import AdapterError, OdooTuiAdapter
from config import ConfigurationError, ConfigurationSource, change_mode as config_change_mode, get_config, read_mode as config_read_mode, register_config
from log_polling import LogPoller, PollingError
from module_update_planning import ModulePlanningError, create_update_plan, plan_module_updates, plan_updates


_sources: dict[str, Path] = {}
_active_update_clients: set[str] = set()
_retained_update_logs: dict[str, float] = {}
_active_update_lock = threading.Lock()
_read_adapter: OdooTuiAdapter | None = None
_operation_boundary: Any | None = None
_CLIENT_SELECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_LOG_ROOT = Path("/home/cyclone/Developer/ODOO/runtime/instances").resolve()


def register_read_adapter(adapter: OdooTuiAdapter) -> None:
    """Register the process-owned read adapter during plugin initialization."""
    global _read_adapter
    if not isinstance(adapter, OdooTuiAdapter):
        raise TypeError("adapter must be an OdooTuiAdapter")
    _read_adapter = adapter


def clear_read_adapter() -> None:
    """Reset the process-local read adapter, primarily for isolated tests."""
    global _read_adapter
    _read_adapter = None


def register_operation_boundary(boundary: Any) -> None:
    """Register the process-owned lifecycle/update boundary at startup.

    The HTTP layer never accepts a boundary, executable, or command from the
    caller.  Deployments that do not provide this capability fail closed.
    """
    global _operation_boundary
    _operation_boundary = boundary


def begin_update_log_window(client: str) -> None:
    with _active_update_lock:
        _active_update_clients.add(client)


def end_update_log_window(client: str) -> None:
    with _active_update_lock:
        _active_update_clients.discard(client)


def retain_update_log_window(client: str, seconds: float = 300.0) -> None:
    with _active_update_lock:
        _retained_update_logs[client] = time.monotonic() + seconds


def update_log_window_active(client: str) -> bool:
    with _active_update_lock:
        deadline = _retained_update_logs.get(client)
        if deadline is not None and deadline <= time.monotonic():
            _retained_update_logs.pop(client, None)
            deadline = None
        return client in _active_update_clients or deadline is not None


def clear_operation_boundary() -> None:
    global _operation_boundary
    _operation_boundary = None


def _operation_model_boundary() -> Any:
    global _operation_boundary
    if _operation_boundary is None:
        from operation_bridge import OperationBridge, OperationBridgeConfig
        _operation_boundary = OperationBridge(OperationBridgeConfig())
    return _operation_boundary


def _read_model_adapter() -> OdooTuiAdapter:
    if _read_adapter is None:
        raise PollingError(503, "ADAPTER_UNAVAILABLE", "The Odoo TUI adapter is unavailable.")
    return _read_adapter


def _require_registered_client(client: str) -> None:
    """Reject unknown resource selectors before dispatching an operation."""
    if not isinstance(client, str) or not client or _CLIENT_SELECTOR_RE.fullmatch(client) is None:
        raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.")
    clients = _read(lambda adapter: adapter.list_clients())
    if not any(item.name == client for item in clients):
        raise PollingError(404, "INSTANCE_NOT_FOUND", "The selected Odoo instance is not registered.")


def _read(call):
    try:
        return call(_read_model_adapter())
    except AdapterError as error:
        raise PollingError(error.status_code, error.code, error.message) from None


def _success(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"protocol_version": 1, "operation": operation, "ok": True, "data": data}


def list_clients() -> dict[str, Any]:
    return _success("clients.list", {"clients": [asdict(item) for item in _read(lambda adapter: adapter.list_clients())]})


def list_releases() -> dict[str, Any]:
    return _success("releases.list", {"releases": [asdict(item) for item in _read(lambda adapter: adapter.list_releases())]})


def get_instance_identity(client: str) -> dict[str, Any]:
    _require_registered_client(client)
    identity = _read(lambda adapter: adapter.get_instance(client))
    if identity.client != client or identity.environment != "local" or not identity.release:
        raise PollingError(404, "STALE_INSTANCE_IDENTITY", "The selected Odoo instance identity is stale.")
    return _success("instance.identity", {"instance": asdict(identity)})


def get_runtime_status(client: str) -> dict[str, Any]:
    _require_registered_client(client)
    return _success("runtime.status", {"status": asdict(_read(lambda adapter: adapter.get_status(client)))})


def get_instance_control(client: str) -> dict[str, Any]:
    _require_registered_client(client)
    return _success("runtime.control", {"control": asdict(_read(lambda adapter: adapter.get_control(client)))})


def register_log_source(client: str, path: str | PathLike[str]) -> None:
    """Register an approved client log path during adapter initialization."""
    if not client or not isinstance(client, str):
        raise ValueError("client must be a non-empty string")
    _sources[client] = Path(path)


def clear_log_sources() -> None:
    """Reset the process-local registry, primarily for isolated tests."""
    _sources.clear()
    with _active_update_lock:
        _active_update_clients.clear()
        _retained_update_logs.clear()


def list_modules(client: str) -> dict[str, Any]:
    """Return the confirmed module catalogue without exposing write operations."""
    _require_registered_client(client)
    return _success("modules.list", {
        "client": client,
        "modules": [
            {**asdict(item), "dependencies": list(item.dependencies)}
            for item in _read(lambda adapter: adapter.list_modules(client))
        ],
    })


def list_databases(client: str) -> dict[str, Any]:
    """Return confirmed database inventory without changing PostgreSQL state."""
    _require_registered_client(client)
    return _success("databases.list", {
        "client": client,
        "databases": [asdict(item) for item in _read(lambda adapter: adapter.list_databases(client))],
    })


def poll_logs(
    *,
    client: str,
    cursor: str | None = None,
    limit: int | str | None = None,
    level: str | None = None,
    pattern: str | None = None,
) -> dict:
    _require_registered_client(client)
    runtime_status = _read(lambda adapter: adapter.get_status(client))
    if runtime_status.state != "online" and not update_log_window_active(client):
        return {"entries": [], "next_cursor": None, "has_more": False, "cursor_reset": False}
    path = _sources.get(client)
    if path is None:
        candidate = (_LOG_ROOT / client / "local" / "logs" / "odoo.log").resolve()
        if not candidate.is_relative_to(_LOG_ROOT) or not candidate.is_file():
            raise PollingError(404, "INSTANCE_NOT_FOUND", "The selected Odoo instance is not registered.")
        path = candidate
        _sources[client] = path
    return LogPoller(path).poll(cursor=cursor, limit=limit, level=level, pattern=pattern, tail=cursor is None)


def query_value(params: Mapping[str, object], name: str) -> str | None:
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        if name == "client" and len(value) != 1:
            raise PollingError(400, "INVALID_REQUEST", "client must be specified exactly once.")
        value = value[0] if value else None
    if name == "client":
        if not isinstance(value, str) or _CLIENT_SELECTOR_RE.fullmatch(value) is None:
            raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.")
        return value
    return None if value is None else str(value)


def register_config_source(client: str, source: ConfigurationSource) -> None:
    """Register the selected instance configuration during adapter setup."""
    register_config(client, source)


def clear_config_sources() -> None:
    from config import clear_configs
    clear_configs()


def read_mode(client: str) -> str:
    try:
        return config_read_mode(client)
    except ConfigurationError as error:
        status = 404 if "unavailable" in str(error) else 422
        raise ConfigurationError(status, "INVALID_CONFIGURATION", str(error)) from None


def change_mode(client: str, mode: object) -> str:
    if not isinstance(mode, str) or mode not in {"client", "database-manager"}:
        raise ConfigurationError(400, "INVALID_MODE", "mode must be client or database-manager.")
    try:
        verified = config_change_mode(client, mode)
    except ConfigurationError as error:
        status = 404 if "unavailable" in str(error) else 422
        raise ConfigurationError(status, "INVALID_CONFIGURATION", str(error)) from None
    if verified != mode:
        raise ConfigurationError(409, "READBACK_MISMATCH", "The startup mode could not be verified.")
    return verified


def require_mode_auth(auth: object, *, write: bool = False) -> None:
    if not _auth_value(auth, "authenticated"):
        raise ConfigurationError(401, "UNAUTHENTICATED", "Authentication is required.")
    if write and not _mode_authorized(auth):
        raise ConfigurationError(403, "FORBIDDEN", "Configuration changes are not authorized.")


def _auth_value(auth: object, name: str) -> object:
    if isinstance(auth, Mapping):
        return auth.get(name)
    return getattr(auth, name, None)


def _authorized(auth: object) -> bool:
    """Apply the host session's authentication and authorization claims.

    Requiring both claims keeps this handler safe when called directly in tests
    or by a future dispatcher that does not enforce manifest metadata.
    """
    if not _auth_value(auth, "authenticated"):
        return False
    if _auth_value(auth, "authorized") is True:
        return True
    permissions = _auth_value(auth, "permissions")
    if isinstance(permissions, (set, frozenset, list, tuple)):
        return "odoo.admin_password.reveal" in permissions
    return False


def _mode_authorized(auth: object) -> bool:
    if _auth_value(auth, "authorized") is True:
        return True
    permissions = _auth_value(auth, "permissions")
    return isinstance(permissions, (set, frozenset, list, tuple)) and "odoo.mode.write" in permissions


def reveal_admin_password(
    *, client: str, auth: object, config_getter: Any = get_config
) -> dict[str, str]:
    """Return a password only for an explicit, authorized capability request."""
    if not _auth_value(auth, "authenticated"):
        raise PollingError(401, "UNAUTHENTICATED", "Authentication is required.")
    if not _authorized(auth):
        raise PollingError(403, "FORBIDDEN", "Authentication and authorization are required.")
    if not isinstance(client, str) or not client:
        raise PollingError(400, "INVALID_CLIENT", "client is required.")
    try:
        value = config_getter(client).get_admin_password()
    except Exception:
        # Configuration implementations are not allowed to cross the HTTP
        # boundary with their exception text: it may contain the secret.
        raise PollingError(404, "PASSWORD_UNAVAILABLE", "The administrator password is unavailable.") from None
    if not isinstance(value, str) or not value.strip():
        raise PollingError(404, "PASSWORD_UNAVAILABLE", "The administrator password is unavailable.")
    return {"admin_passwd": value}


def _call_with_timeout(function: Any, timeout_seconds: float) -> tuple[bool, Any]:
    """Run an injected boundary call without allowing it to block the request."""
    if timeout_seconds <= 0:
        return False, None
    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(function())
        except BaseException as error:  # boundary errors are mapped by the caller
            failure.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        return False, None
    if failure:
        raise failure[0]
    return True, result[0] if result else None


def _lifecycle_state(boundary: Any, client: str) -> str | None:
    value = boundary.status(client)
    if isinstance(value, Mapping):
        state = value.get("state", value.get("status"))
        return state.lower() if isinstance(state, str) else None
    return None


def _approval(approval: Any, client: str, plan: Mapping[str, Any]) -> bool:
    if isinstance(approval, bool):
        return approval
    if not callable(approval):
        return False
    try:
        return approval(client, plan) is True
    except Exception:
        return False


def _result_state(boundary: Any, client: str) -> str | None:
    try:
        return _lifecycle_state(boundary, client)
    except Exception:
        return None


def _lifecycle_result(client: str, boundary: Any, operation: str, expected: str, approval: Any,
                      timeout_seconds: float) -> dict[str, Any]:
    try:
        state = _lifecycle_state(boundary, client)
    except Exception:
        return {"ok": False, "code": "status_failure", "client": client}
    if state != ("running" if operation in {"stop", "restart"} else "stopped"):
        return {"ok": False, "code": "invalid_state", "client": client, "state": state}
    if not _approval(approval, client, {"operation": operation}):
        return {"ok": False, "code": "approval_denied", "client": client, "state": state}
    try:
        completed, _ = _call_with_timeout(lambda: getattr(boundary, operation)(client), timeout_seconds)
        if not completed:
            return {"ok": False, "code": "timeout", "client": client, "state": _result_state(boundary, client)}
    except Exception:
        return {"ok": False, "code": "process_failure", "client": client, "state": _result_state(boundary, client)}
    final_state = _result_state(boundary, client)
    if final_state != expected:
        return {"ok": False, "code": "readback_mismatch", "client": client, "state": final_state}
    return {"ok": True, "code": operation, "client": client, "state": final_state}


def start_instance(client: str, boundary: Any, approval: Any, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    return _lifecycle_result(client, boundary, "start", "running", approval, timeout_seconds)


def stop_instance(client: str, boundary: Any, approval: Any, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    return _lifecycle_result(client, boundary, "stop", "stopped", approval, timeout_seconds)


def restart_instance(client: str, boundary: Any, approval: Any, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    return _lifecycle_result(client, boundary, "restart", "running", approval, timeout_seconds)


def _validate_update_plan(plan: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    if not isinstance(plan, Mapping) or plan.get("ok") is not True or plan.get("requires_approval") is not True:
        return None, {"ok": False, "code": "invalid_plan"}
    if plan.get("kind") not in {"selected", "update_all"}:
        return None, {"ok": False, "code": "invalid_plan"}
    if plan.get("stale") is True:
        return None, {"ok": False, "code": "stale_plan"}
    expires_at = plan.get("expires_at")
    if expires_at is not None and not isinstance(expires_at, (int, float)):
        return None, {"ok": False, "code": "invalid_plan"}
    if expires_at is not None and expires_at <= time.time():
        return None, {"ok": False, "code": "stale_plan"}
    entries = plan.get("modules")
    if not isinstance(entries, list) or not entries or any(not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not item["name"] for item in entries):
        return None, {"ok": False, "code": "invalid_plan"}
    names = [item["name"] for item in entries]
    if plan.get("module_names", names) != names or plan.get("count", len(names)) != len(names):
        return None, {"ok": False, "code": "invalid_plan"}
    return names, None


def apply_module_updates(client: str, plan: Mapping[str, Any], boundary: Any, approval: Any,
                         *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Apply one approved plan through the injected backend and verify read-back."""
    names, rejection = _validate_update_plan(plan)
    if rejection:
        return rejection
    assert names is not None
    try:
        state = _lifecycle_state(boundary, client)
    except Exception:
        return {"ok": False, "code": "status_failure", "client": client, "modules": names}
    if state != "running":
        return {"ok": False, "code": "invalid_state", "client": client, "modules": names, "state": state}
    if not _approval(approval, client, plan):
        return {"ok": False, "code": "approval_denied", "client": client, "modules": names, "state": state}
    update_many = getattr(boundary, "update_modules", None)
    update_one = getattr(boundary, "update_module", None)
    update = update_many if callable(update_many) else getattr(boundary, "update", None)
    if not callable(update) and not callable(update_one):
        return {"ok": False, "code": "operation_not_supported", "client": client, "modules": names, "state": state}
    try:
        if callable(update_many):
            operation = lambda: update_many(client, names)
        elif callable(update):
            operation = lambda: update(client, names)
        else:
            assert callable(update_one)
            operation = lambda: {"updated": [name for name in names if update_one(client, name) is not False]}
        completed, value = _call_with_timeout(operation, timeout_seconds)
        if not completed:
            return {"ok": False, "code": "timeout", "client": client, "modules": names, "state": _result_state(boundary, client)}
    except Exception:
        return {"ok": False, "code": "update_failure", "client": client, "modules": names, "state": _result_state(boundary, client)}
    final_state = _result_state(boundary, client)
    if final_state != "running":
        return {"ok": False, "code": "readback_mismatch", "client": client, "modules": names, "state": final_state}
    updated = value.get("updated", names) if isinstance(value, Mapping) else names
    failed = value.get("failed", []) if isinstance(value, Mapping) else []
    if failed:
        return {"ok": False, "code": "partial_failure", "client": client, "modules": names, "updated": list(updated), "failed": list(failed), "state": final_state}
    return {"ok": True, "code": "updated", "client": client, "modules": names, "updated": list(updated), "state": final_state}


apply_update_plan = apply_module_updates
update_modules = apply_module_updates
