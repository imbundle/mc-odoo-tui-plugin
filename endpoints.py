"""HTTP adapters for the external Mission Control plugin."""
from __future__ import annotations

from collections.abc import Mapping
import re
import time
from typing import Any, TypeAlias, TypedDict

if __package__:
    from . import handlers, runtime_bootstrap
    from .log_polling import PollingError
else:
    import handlers
    import runtime_bootstrap
    from log_polling import PollingError

RequestBody: TypeAlias = dict[str, Any]
QueryParams: TypeAlias = Mapping[str, object]
_DIRECT_AUTH_MISSING = object()


def _public(result: dict[str, Any]) -> dict[str, Any]:
    """Expose typed data; the strict worker envelope remains internal."""
    return result["data"]


class HealthResponse(TypedDict):
    """Stable response returned by the authenticated plugin health check."""

    ok: bool
    available: bool
    plugin: str


def health(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> HealthResponse:
    """Confirm plugin availability without consulting or mutating Odoo state."""
    if auth is None:
        raise PollingError(401, "UNAUTHENTICATED", "Authentication is required.")
    _require_authenticated(auth)
    if not isinstance(body, dict) or body or not isinstance(params, Mapping) or params:
        raise PollingError(400, "INVALID_REQUEST", "health does not accept request data.")
    return {"ok": True, "available": True, "plugin": "odoo-tui"}


def getLogs(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return one bounded log batch through the plugin-owned service boundary."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    if not client:
        raise PollingError(400, "INVALID_CLIENT", "client is required.")
    return handlers.poll_logs(
        client=client,
        cursor=handlers.query_value(params, "cursor"),
        limit=handlers.query_value(params, "limit"),
        level=handlers.query_value(params, "level"),
        pattern=handlers.query_value(params, "pattern"),
    )


def revealAdminPassword(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    """Reveal the password only through the dedicated authenticated endpoint."""
    # The client is a required query selector, not a caller-controlled body
    # field. Keeping the body out of this capability prevents accidental
    # cross-instance selection through a generic dispatcher.
    client = handlers.query_value(params, "client")
    return handlers.reveal_admin_password(client=client or "", auth=auth)


def getMode(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    if auth is _DIRECT_AUTH_MISSING:
        handlers.require_mode_auth(auth)
    else:
        _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    if not client:
        raise handlers.ConfigurationError(400, "INVALID_CLIENT", "client is required.")
    return {"client": client, "mode": handlers.read_mode(client)}


def setMode(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    handlers.require_mode_auth(auth, write=True)
    client = handlers.query_value(params, "client")
    if not client:
        raise handlers.ConfigurationError(400, "INVALID_CLIENT", "client is required.")
    if not isinstance(body, dict) or set(body) != {"mode"}:
        raise handlers.ConfigurationError(400, "INVALID_REQUEST", "request body must contain only mode.")
    mode = handlers.change_mode(client, body["mode"])
    return {"client": client, "mode": mode, "verified": True}


def getModules(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return the selected client's typed, read-only module catalogue."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    return _public(handlers.list_modules(client))


def getDatabases(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return the selected client's confirmed, read-only database inventory."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    return _public(handlers.list_databases(client))


def listClients(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """List registered clients through the typed read-only adapter."""
    _require_authenticated(auth)
    return _public(handlers.list_clients())


def listReleases(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """List installed Odoo releases through the typed read-only adapter."""
    _require_authenticated(auth)
    return _public(handlers.list_releases())


def getSnapshot(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return one coherent registry or selected-client read snapshot."""
    if auth is _DIRECT_AUTH_MISSING:
        raise PollingError(401, "UNAUTHENTICATED", "Authentication is required.")
    _require_authenticated(auth)
    if not isinstance(body, dict) or body or not isinstance(params, Mapping):
        raise PollingError(400, "INVALID_REQUEST", "snapshot accepts only an empty GET body.")
    if any(name != "client" for name in params):
        raise PollingError(400, "INVALID_REQUEST", "snapshot accepts only the client selector.")
    value = params.get("client")
    if isinstance(value, (list, tuple)):
        if len(value) != 1 or not isinstance(value[0], str) or not value[0]:
            raise PollingError(400, "INVALID_REQUEST", "client must be specified exactly once.")
        value = value[0]
    if value is not None and not isinstance(value, str):
        raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.")
    if "client" in params and value == "":
        raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.")
    if value:
        try:
            client_size = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.") from error
        if (
            client_size > 128
            or handlers._CLIENT_SELECTOR_RE.fullmatch(value) is None
            or value in {"constructor", "prototype", "__proto__"}
        ):
            raise PollingError(400, "INVALID_REQUEST", "client must be a valid selector.")
    try:
        if value:
            return handlers.snapshot_selected(value)
        return handlers.snapshot_collection()
    except PollingError:
        raise
    except Exception as error:
        raise PollingError(503, "SNAPSHOT_UNAVAILABLE", "The snapshot is unavailable.") from error


def getInstanceIdentity(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return the selected instance identity without exposing transport details."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    return _public(handlers.get_instance_identity(client))


def getInstanceStatus(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return confirmed runtime status and PID for the selected instance."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    return _public(handlers.get_runtime_status(client))


def getInstanceControl(body: RequestBody, params: QueryParams, auth: object = _DIRECT_AUTH_MISSING) -> dict:
    """Return trusted lifecycle eligibility separately from legacy runtime status."""
    _require_authenticated(auth)
    client = handlers.query_value(params, "client")
    return _public(handlers.get_instance_control(client))


def _require_operation_auth(auth: object) -> None:
    if auth is None:
        return
    authenticated = auth.get("authenticated") if isinstance(auth, Mapping) else getattr(auth, "authenticated", None)
    authorized = auth.get("authorized") if isinstance(auth, Mapping) else getattr(auth, "authorized", None)
    if authenticated is not True:
        raise PollingError(401, "UNAUTHENTICATED", "Authentication is required.")
    if authorized is not True:
        raise PollingError(403, "FORBIDDEN", "Lifecycle operations are not authorized.")


def _require_authenticated(auth: object) -> None:
    """Host transport authenticates before dispatch; direct calls must opt in."""
    if auth is None:
        return
    authenticated = auth.get("authenticated") if isinstance(auth, Mapping) else getattr(auth, "authenticated", None)
    if authenticated is not True:
        raise PollingError(401, "UNAUTHENTICATED", "Authentication is required.")


def _operation_client(params: QueryParams) -> str:
    client = handlers.query_value(params, "client")
    if not client:
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    return client


def _operation_error(error: Any) -> None:
    statuses = {
        "INVALID_REQUEST": 400,
        "CONFIRMATION_REQUIRED": 400,
        "INSTANCE_NOT_FOUND": 404,
        "PRECONDITION_FAILED": 409,
        "READBACK_MISMATCH": 409,
        "OPERATION_IN_PROGRESS": 409,
        "OPERATION_UNAVAILABLE": 503,
        "WORKER_PROTOCOL_ERROR": 503,
        "OPERATION_TIMED_OUT": 504,
        "OPERATION_FAILED": 502,
    }
    details = {
        "INVALID_REQUEST": "invalid operation request",
        "CONFIRMATION_REQUIRED": "invalid operation request",
        "INSTANCE_NOT_FOUND": "requested instance was not found",
        "PRECONDITION_FAILED": "operation precondition was not met",
        "READBACK_MISMATCH": "operation precondition was not met",
        "OPERATION_IN_PROGRESS": "another Odoo operation is in progress",
        "OPERATION_UNAVAILABLE": "operation bridge is unavailable",
        "WORKER_PROTOCOL_ERROR": "operation bridge is unavailable",
        "OPERATION_TIMED_OUT": "operation outcome is indeterminate; refresh status",
        "OPERATION_FAILED": "operation did not complete successfully",
    }
    code = getattr(error, "code", "WORKER_PROTOCOL_ERROR")
    raise PollingError(statuses.get(code, 503), code, details.get(code, "operation bridge is unavailable")) from None


def _invoke_operation(request: dict[str, Any], precondition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    log_window = request["operation"] == "lifecycle.restart" and ("modules" in request or request.get("update_all") is True)
    if log_window:
        handlers.begin_update_log_window(request["client"])
    succeeded = False
    try:
        try:
            response = (handlers.invoke_operation(request, precondition)
                        if precondition is not None
                        else handlers._operation_model_boundary().invoke(request))
        except Exception as error:
            if isinstance(getattr(error, "code", None), str) and isinstance(getattr(error, "message", None), str):
                _operation_error(error)
            raise PollingError(503, "OPERATION_UNAVAILABLE", "operation bridge is unavailable") from None
        if not isinstance(response, Mapping) or set(response) != {"protocol_version", "operation", "ok", "data"} or response.get("ok") is not True or not isinstance(response.get("data"), dict):
            raise PollingError(503, "WORKER_PROTOCOL_ERROR", "operation bridge is unavailable")
        if response["protocol_version"] != 1 or response["operation"] != request["operation"]:
            raise PollingError(503, "WORKER_PROTOCOL_ERROR", "operation bridge is unavailable")
        succeeded = True
        return response["data"]
    finally:
        if log_window:
            if not succeeded:
                handlers.retain_update_log_window(request["client"])
            handlers.end_update_log_window(request["client"])


def _module_selector(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    if any(not isinstance(name, str) or re.fullmatch(r"^[a-z][a-z0-9_]*$", name) is None for name in value) or len(set(value)) != len(value):
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    return list(value)


def _startup_mode(value: object) -> str:
    if value not in {"client", "database_manager"}:
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    return str(value)


def _lifecycle_endpoint(operation: str, body: RequestBody, params: QueryParams, auth: object) -> dict:
    _require_operation_auth(auth)
    client = _operation_client(params)
    handlers._require_registered_client(client)
    if not isinstance(body, dict) or "confirmation" not in body:
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    confirmation = body["confirmation"]
    precondition = body.get("precondition")
    if not isinstance(precondition, Mapping):
        raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
    expected = f"{operation.upper()} {client}"
    if confirmation != expected:
        raise PollingError(400, "CONFIRMATION_REQUIRED", "invalid operation request")
    mode = _startup_mode(body["mode"]) if "mode" in body else None
    if mode is not None and operation == "stop":
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    selector: dict[str, Any] = {}
    extra = set(body) - {"confirmation", "mode", "precondition"}
    if operation == "restart":
        if extra == set():
            pass
        elif extra == {"modules"}:
            selector["modules"] = _module_selector(body["modules"])
        elif extra == {"update_all"} and body["update_all"] is True:
            selector["update_all"] = True
        else:
            raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    elif extra:
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    return _invoke_operation({
        "protocol_version": 1,
        "operation": f"lifecycle.{operation}",
        "client": client,
        "environment": "local",
        "confirmation": confirmation,
        **({"mode": mode} if mode is not None else {}),
        "precondition": dict(precondition),
        **selector,
    }, precondition)


def startInstance(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    return _lifecycle_endpoint("start", body, params, auth)


def stopInstance(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    return _lifecycle_endpoint("stop", body, params, auth)


def restartInstance(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    return _lifecycle_endpoint("restart", body, params, auth)


def _update_selector(body: RequestBody, *, apply: bool) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
    if set(body) == {"update_all"} and body["update_all"] is True and not apply:
        return {"update_all": True}
    if set(body) == {"modules"} and not apply:
        return {"modules": _module_selector(body["modules"])}
    if apply and set(body) in ({"update_all", "confirmation", "precondition"}, {"modules", "confirmation", "precondition"}):
        precondition = body.get("precondition")
        if not isinstance(precondition, Mapping):
            raise PollingError(409, "PRECONDITION_FAILED", "The selected Odoo instance changed.")
        selector = {"precondition": dict(precondition), "confirmation": body["confirmation"]}
        if body.get("update_all") is True:
            selector["update_all"] = True
        elif "modules" in body:
            selector["modules"] = _module_selector(body["modules"])
        else:
            raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
        if not isinstance(selector["confirmation"], str) or not selector["confirmation"]:
            raise PollingError(400, "INVALID_REQUEST", "invalid operation request")
        return selector
    raise PollingError(400, "INVALID_REQUEST", "invalid operation request")


def planModuleUpdates(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    _require_operation_auth(auth)
    client = _operation_client(params)
    handlers._require_registered_client(client)
    selector = _update_selector(body, apply=False)
    return _invoke_operation({"protocol_version": 1, "operation": "updates.plan", "client": client, "environment": "local", **selector})


def applyModuleUpdates(body: RequestBody, params: QueryParams, auth: object = None) -> dict:
    _require_operation_auth(auth)
    client = _operation_client(params)
    handlers._require_registered_client(client)
    selector = _update_selector(body, apply=True)
    precondition = selector.pop("precondition")
    return _invoke_operation({"protocol_version": 1, "operation": "updates.apply", "client": client, "environment": "local", **selector, "precondition": precondition}, precondition)


# Importing the production endpoints is the telemetry plugin initialization
# boundary. Bootstrap is eager and remains fail-closed when deployment inputs
# are unavailable; fixture transports may replace it in isolated tests.
runtime_bootstrap.bootstrap_adapter()
_production_adapter = runtime_bootstrap.adapter()
if _production_adapter is not None:
    handlers.register_read_adapter(_production_adapter)
