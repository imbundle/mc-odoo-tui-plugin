"""Plugin-owned log source registry and polling service boundary."""
from __future__ import annotations

import threading
import time
import re
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, wait
from os import PathLike
from pathlib import Path
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

if __package__:
    from .adapters.odoo_tui_adapter import AdapterError, OdooTuiAdapter
    from .config import ConfigurationError, ConfigurationSource, change_mode as config_change_mode, get_config, read_mode as config_read_mode, register_config
    from .log_polling import LogPoller, PollingError
    from .module_update_planning import ModulePlanningError, create_update_plan, plan_module_updates, plan_updates
else:
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
_SNAPSHOT_PROCESS_INSTANCE_ID = uuid.uuid4().hex
_snapshot_composition_lock = threading.Lock()
_snapshot_registry_key: tuple[tuple[str, str], ...] | None = None
_snapshot_registry_epoch = 0
_snapshot_worker_lock = threading.Lock()
_mutation_dispatch_lock = threading.Lock()
_snapshot_active_workers = 0
_MAX_CLIENTS = 32
_MAX_RELEASES = 64
_MAX_MODULES = 500
_MAX_DATABASES = 64
_MAX_DEPENDENCIES = 128
_MAX_STRING_BYTES = 512
_MAX_IDENTITY_BYTES = 4096
_MAX_PAYLOAD_BYTES = 256 * 1024
_MAX_COMPOSITION_SECONDS = 12.0


class _SnapshotTimeout(FutureTimeoutError):
    def __init__(self, release: Any):
        super().__init__()
        self.release = release


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
        if __package__:
            from .operation_bridge import OperationBridge, OperationBridgeConfig
        else:
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


def _check_mutation_precondition_unlocked(client: str, expected: Mapping[str, Any]) -> None:
    if set(expected) != {"registry_identity", "process_instance_id", "registry_epoch"}:
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
    process_instance_id = expected["process_instance_id"]
    registry_identity = expected["registry_identity"]
    registry_epoch = expected["registry_epoch"]
    if not isinstance(process_instance_id, str) or re.fullmatch(r"[0-9a-f]{32}", process_instance_id) is None:
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
    if not isinstance(registry_identity, str) or len(registry_identity.encode("utf-8")) > _MAX_IDENTITY_BYTES:
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
    if not isinstance(registry_epoch, int) or isinstance(registry_epoch, bool) or registry_epoch < 0:
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
    registry, _collection_identity, current_epoch = _snapshot_registry(_read_model_adapter())
    current = next((item for item in registry if item["name"] == client), None)
    if (current is None or current["registry_identity"] != registry_identity
        or process_instance_id != _SNAPSHOT_PROCESS_INSTANCE_ID or registry_epoch != current_epoch):
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")


def check_mutation_precondition(client: str, expected: Mapping[str, Any]) -> None:
    with _snapshot_composition_lock:
        _check_mutation_precondition_unlocked(client, expected)


def invoke_operation(request: Mapping[str, Any], precondition: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and dispatch a mutation without releasing the target lock."""
    with _mutation_dispatch_lock, _snapshot_composition_lock:
        _check_mutation_precondition_unlocked(str(request["client"]), precondition)
        return _operation_model_boundary().invoke(dict(request))


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
            {key: value for key, value in {**asdict(item), "dependencies": list(item.dependencies)}.items() if value is not None or key in {"version", "installable"}}
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


def _snapshot_registry(adapter: OdooTuiAdapter) -> tuple[list[dict[str, Any]], str, int]:
    global _snapshot_registry_key, _snapshot_registry_epoch
    clients = adapter.list_clients()
    if len(clients) > _MAX_CLIENTS:
        raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    registry: list[dict[str, Any]] = []
    key: list[tuple[str, str]] = []
    for item in clients:
        for value in (item.name, item.release, item.environment, item.local_url):
            if value is not None and len(value.encode("utf-8")) > _MAX_STRING_BYTES:
                raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
        identity = json.dumps(
            [item.name, item.release, item.environment, item.local_url],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(identity.encode("utf-8")) > _MAX_IDENTITY_BYTES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
        registry.append({
            "name": item.name,
            "release": item.release,
            "environment": item.environment,
            "local_url": item.local_url,
            "registry_identity": identity,
        })
        key.append((item.name, identity))
    canonical_key = tuple(key)
    if _snapshot_registry_key != canonical_key:
        if _snapshot_registry_key is not None:
            _snapshot_registry_epoch += 1
        _snapshot_registry_key = canonical_key
    registry_identity = json.dumps(
        [[name, identity] for name, identity in canonical_key],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    if len(registry_identity.encode("utf-8")) > _MAX_IDENTITY_BYTES:
        raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    return registry, registry_identity, _snapshot_registry_epoch


def _snapshot_error_code(error: Exception, capability: str) -> str:
    if isinstance(error, FutureTimeoutError):
        return "SNAPSHOT_TIMEOUT"
    code = getattr(error, "transport_code", None) or getattr(error, "code", "")
    if code == "MALFORMED_RESPONSE":
        return "MALFORMED_BACKEND_RESPONSE"
    if code == "OUTPUT_TOO_LARGE":
        return "SNAPSHOT_TOO_LARGE"
    if code == "STATUS_PROBE_FAILED":
        return {
            "status": "STATUS_UNAVAILABLE",
            "modules": "MODULES_UNAVAILABLE",
            "databases": "DATABASES_UNAVAILABLE",
        }.get(capability, "SNAPSHOT_UNAVAILABLE")
    if capability == "registry":
        return "REGISTRY_UNAVAILABLE"
    return {
        "releases": "RELEASES_UNAVAILABLE",
        "identity": "IDENTITY_UNAVAILABLE",
        "status": "STATUS_UNAVAILABLE",
        "control": "CONTROL_UNAVAILABLE",
        "modules": "MODULES_UNAVAILABLE",
        "databases": "DATABASES_UNAVAILABLE",
    }.get(capability, "SNAPSHOT_UNAVAILABLE")


def _snapshot_phase_timeout(deadline: float, budget: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.")
    return min(remaining, budget)


def _snapshot_json_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload).encode("utf-8"))


def _snapshot_releases(values: Any) -> list[dict[str, str]]:
    if len(values) > _MAX_RELEASES:
        raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    result = []
    for item in values:
        if len(item.version.encode("utf-8")) > _MAX_STRING_BYTES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
        result.append({"version": item.version})
    return result


def _snapshot_check_texts(values: Any) -> None:
    for value in values:
        if value is not None and len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")


def _snapshot_adapter() -> OdooTuiAdapter:
    try:
        return _read_model_adapter()
    except PollingError as error:
        if error.code == "ADAPTER_UNAVAILABLE":
            raise PollingError(503, "REGISTRY_UNAVAILABLE", "The client registry is unavailable.") from error
        raise


def _snapshot_worker_call(executor: ThreadPoolExecutor, operation: Any):
    global _snapshot_active_workers
    with _snapshot_worker_lock:
        _snapshot_active_workers += 1
    try:
        future = executor.submit(_snapshot_worker_wrapper, operation)
        future.add_done_callback(_snapshot_worker_finished)
        return future
    except Exception:
        with _snapshot_worker_lock:
            _snapshot_active_workers -= 1
        raise


def _snapshot_worker_finished(_future: Any) -> None:
    global _snapshot_active_workers
    with _snapshot_worker_lock:
        _snapshot_active_workers = max(0, _snapshot_active_workers - 1)


def _snapshot_worker_wrapper(operation: Any):
    return operation()


def _snapshot_has_active_workers() -> bool:
    with _snapshot_worker_lock:
        return _snapshot_active_workers > 0


def _snapshot_defer_lock_release(futures: Any) -> Any:
    pending = list(futures)
    state_lock = threading.Lock()
    released = False
    allowed = False

    def release_when_done(_future: Any = None) -> None:
        nonlocal released
        with state_lock:
            if not allowed or released or not all(future.done() for future in pending):
                return
            released = True
        _snapshot_composition_lock.release()

    for future in pending:
        future.add_done_callback(release_when_done)

    def allow_release() -> None:
        nonlocal allowed
        with state_lock:
            allowed = True
        release_when_done()

    return allow_release


def _snapshot_bounded_call(operation: Any, timeout: float):
    executor = ThreadPoolExecutor(max_workers=1)
    future = _snapshot_worker_call(executor, operation)
    try:
        done, _ = wait((future,), timeout=timeout)
        if not done:
            release = _snapshot_defer_lock_release((future,))
            raise _SnapshotTimeout(release)
        return future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def snapshot_collection() -> dict[str, Any]:
    if not _snapshot_composition_lock.acquire(blocking=False):
        raise PollingError(503, "SNAPSHOT_BUSY", "The snapshot is busy.")
    if _snapshot_has_active_workers():
        _snapshot_composition_lock.release()
        raise PollingError(503, "SNAPSHOT_BUSY", "The snapshot is busy.")
    defer_lock_release = False
    allow_deferred_release: Any | None = None
    composition_deadline = time.monotonic() + _MAX_COMPOSITION_SECONDS
    try:
        adapter = _snapshot_adapter()
        try:
            registry, registry_identity, epoch = _snapshot_bounded_call(lambda: _snapshot_registry(adapter), _snapshot_phase_timeout(composition_deadline, 5.0))
        except PollingError:
            raise
        except _SnapshotTimeout as error:
            defer_lock_release = True
            allow_deferred_release = error.release
            raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.") from error
        except FutureTimeoutError as error:
            raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.") from error
        except AdapterError as error:
            if getattr(error, "transport_code", None) == "OUTPUT_TOO_LARGE":
                raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.") from error
            raise PollingError(503, "REGISTRY_UNAVAILABLE", "The client registry is unavailable.") from error
        except Exception as error:
            raise PollingError(503, "REGISTRY_UNAVAILABLE", "The client registry is unavailable.") from error
        releases: list[dict[str, Any]] = []
        releases_error: dict[str, str] | None = None
        executor = ThreadPoolExecutor(max_workers=1)
        future = _snapshot_worker_call(executor, adapter.list_releases)
        try:
            done, _ = wait((future,), timeout=max(0.0, min(5.5, composition_deadline - time.monotonic())))
            if not done:
                raise FutureTimeoutError()
            releases = _snapshot_releases(future.result())
        except PollingError:
            raise
        except Exception as error:
            if isinstance(error, FutureTimeoutError):
                defer_lock_release = True
                allow_deferred_release = _snapshot_defer_lock_release((future,))
            releases_error = {"code": _snapshot_error_code(error, "releases")}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        _snapshot_phase_timeout(composition_deadline, float("inf"))
        payload = {
            "protocol_version": 1,
            "process_instance_id": _SNAPSHOT_PROCESS_INSTANCE_ID,
            "registry_epoch": epoch,
            "registry_identity": registry_identity,
            "clients": registry,
            "releases": releases,
            "releases_error": releases_error,
            "errors": {},
        }
        if _snapshot_json_size(payload) > _MAX_PAYLOAD_BYTES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
        _snapshot_phase_timeout(composition_deadline, float("inf"))
        return payload
    finally:
        if defer_lock_release:
            if allow_deferred_release is not None:
                allow_deferred_release()
        else:
            _snapshot_composition_lock.release()


def _selected_identity(value: Any) -> dict[str, Any]:
    _snapshot_check_texts((value.client, value.release, value.environment, value.database))
    return {
        "client": value.client,
        "release": value.release,
        "environment": value.environment,
        "database": value.database,
        "http_port": value.http_port,
        "longpolling_port": value.longpolling_port,
    }


def _selected_status(value: Any) -> dict[str, Any]:
    _snapshot_check_texts((value.state, value.process_name, value.startup_mode))
    return {
        "state": value.state,
        "pid": value.pid,
        "process_name": value.process_name,
        "pm2_id": value.pm2_id,
        "startup_mode": value.startup_mode,
    }


def _selected_control(value: Any) -> dict[str, Any]:
    _snapshot_check_texts((value.control_mode, value.reason))
    return {
        "control_mode": value.control_mode,
        "lifecycle_eligible": value.lifecycle_eligible,
        "reason": value.reason,
    }


def _selected_modules(values: Any) -> list[dict[str, Any]]:
    if len(values) > _MAX_MODULES:
        raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    for item in values:
        _snapshot_check_texts((item.name, getattr(item, "display_name", None), item.version, *item.dependencies))
        if len(item.dependencies) > _MAX_DEPENDENCIES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    return [
        {
            "name": item.name,
            **({"display_name": getattr(item, "display_name", None)} if getattr(item, "display_name", None) is not None else {}),
            "version": item.version,
            "installed": item.installed,
            "installable": item.installable,
            "update_available": item.update_available,
            "dependencies": list(item.dependencies),
        }
        for item in values
    ]


def _selected_databases(values: Any) -> list[dict[str, Any]]:
    if len(values) > _MAX_DATABASES:
        raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
    for item in values:
        _snapshot_check_texts((item.name,))
    return [{"name": item.name, "exists": item.exists} for item in values]


def snapshot_selected(client: str) -> dict[str, Any]:
    if not _snapshot_composition_lock.acquire(blocking=False):
        raise PollingError(503, "SNAPSHOT_BUSY", "The snapshot is busy.")
    if _snapshot_has_active_workers():
        _snapshot_composition_lock.release()
        raise PollingError(503, "SNAPSHOT_BUSY", "The snapshot is busy.")
    defer_lock_release = False
    allow_deferred_release: Any | None = None
    composition_deadline = time.monotonic() + _MAX_COMPOSITION_SECONDS
    try:
        adapter = _snapshot_adapter()
        try:
            registry, registry_identity, epoch = _snapshot_bounded_call(lambda: _snapshot_registry(adapter), _snapshot_phase_timeout(composition_deadline, 5.0))
        except PollingError:
            raise
        except _SnapshotTimeout as error:
            defer_lock_release = True
            allow_deferred_release = error.release
            raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.") from error
        except FutureTimeoutError as error:
            raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.") from error
        except AdapterError as error:
            if getattr(error, "transport_code", None) == "OUTPUT_TOO_LARGE":
                raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.") from error
            raise PollingError(503, "REGISTRY_UNAVAILABLE", "The client registry is unavailable.") from error
        except Exception as error:
            raise PollingError(503, "REGISTRY_UNAVAILABLE", "The client registry is unavailable.") from error
        selected = next((item for item in registry if item["name"] == client), None)
        if selected is None:
            raise PollingError(404, "INSTANCE_NOT_FOUND", "The selected Odoo instance is not registered.")
        operations = {
            "releases": lambda: adapter.list_releases(),
            "identity": lambda: adapter.get_instance(client),
            "status": lambda: adapter.get_status(client),
            "control": lambda: adapter.get_control(client),
            "modules": lambda: adapter.list_modules(client),
            "databases": lambda: adapter.list_databases(client),
        }
        values: dict[str, Any] = {}
        errors: dict[str, dict[str, dict[str, str]]] = {client: {}}
        executor = ThreadPoolExecutor(max_workers=6)
        futures = {name: _snapshot_worker_call(executor, operation) for name, operation in operations.items()}
        try:
            done, not_done = wait(tuple(futures.values()), timeout=max(0.0, min(6.0, composition_deadline - time.monotonic())))
            if not_done:
                defer_lock_release = True
                allow_deferred_release = _snapshot_defer_lock_release(tuple(futures.values()))
                raise PollingError(503, "SNAPSHOT_TIMEOUT", "The snapshot timed out.")
            for name, future in futures.items():
                try:
                    if future not in done:
                        raise FutureTimeoutError()
                    values[name] = future.result()
                except Exception as error:
                    values[name] = None
                    errors[client][name] = {"code": _snapshot_error_code(error, name)}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        releases = [] if values["releases"] is None else _snapshot_releases(values["releases"])
        releases_error = errors[client].pop("releases", None)
        identity_value = values["identity"]
        if identity_value is not None and (identity_value.client != client or identity_value.environment != selected["environment"] or identity_value.release != selected["release"]):
            values["identity"] = None
            errors[client]["identity"] = {"code": "MALFORMED_BACKEND_RESPONSE"}
        snapshot = {
            "registry_identity": selected["registry_identity"],
            "identity": None if values["identity"] is None else _selected_identity(values["identity"]),
            "status": None if values["status"] is None else _selected_status(values["status"]),
            "control": None if values["control"] is None else _selected_control(values["control"]),
            "modules": None if values["modules"] is None else _selected_modules(values["modules"]),
            "databases": None if values["databases"] is None else _selected_databases(values["databases"]),
        }
        _snapshot_phase_timeout(composition_deadline, float("inf"))
        payload = {
            "protocol_version": 1,
            "process_instance_id": _SNAPSHOT_PROCESS_INSTANCE_ID,
            "registry_epoch": epoch,
            "registry_identity": registry_identity,
            "releases": releases,
            "releases_error": releases_error,
            "client": client,
            "snapshot": snapshot,
            "errors": errors if errors[client] else {},
        }
        if _snapshot_json_size(payload) > _MAX_PAYLOAD_BYTES:
            raise PollingError(503, "SNAPSHOT_TOO_LARGE", "The snapshot is too large.")
        _snapshot_phase_timeout(composition_deadline, float("inf"))
        return payload
    finally:
        if defer_lock_release:
            if allow_deferred_release is not None:
                allow_deferred_release()
        else:
            _snapshot_composition_lock.release()


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
    if __package__:
        from .config import clear_configs
    else:
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
