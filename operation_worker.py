"""Strict protocol and controller worker for plugin-owned Odoo mutations."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

_PROTOCOL_VERSION = 1
_CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_MODULE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_COMMON_KEYS = {"protocol_version", "operation", "client", "environment"}
_MODES = {"client": "Client", "database_manager": "Database Manager"}
_LIFECYCLE = {
    "lifecycle.start": "START",
    "lifecycle.stop": "STOP",
    "lifecycle.restart": "RESTART",
}


class _UpdateLogVerifier:
    """Keep known non-fatal Odoo warnings visible without blocking restart."""

    _NON_BLOCKING_WARNING = "error-prone use of @class"

    def __init__(self, delegate: Any):
        self._delegate = delegate

    def capture(self, *args: Any, **kwargs: Any):
        return self._delegate.capture(*args, **kwargs)

    def analyze(self, checkpoint: Any):
        report = self._delegate.analyze(checkpoint)
        filtered = tuple(
            line for line in report.new_errors
            if not (
                self._NON_BLOCKING_WARNING in line.lower()
                and "warning" in line.lower()
            )
        )
        return replace(report, new_errors=filtered)


class ProtocolError(ValueError):
    """A request does not match the closed worker protocol."""


def _require_common(request: object) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ProtocolError("request must be an object")
    if set(request) & _COMMON_KEYS != _COMMON_KEYS:
        raise ProtocolError("request is missing required fields")
    if request["protocol_version"] != _PROTOCOL_VERSION or isinstance(request["protocol_version"], bool):
        raise ProtocolError("unsupported protocol version")
    if not isinstance(request["operation"], str):
        raise ProtocolError("operation is required")
    if not isinstance(request["client"], str) or _CLIENT_RE.fullmatch(request["client"]) is None:
        raise ProtocolError("client is not a valid registered identifier")
    if request["environment"] != "local":
        raise ProtocolError("environment must be local")
    return dict(request)


def _modules(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ProtocolError("modules must contain between 1 and 100 names")
    if any(not isinstance(name, str) or _MODULE_RE.fullmatch(name) is None for name in value):
        raise ProtocolError("modules must contain canonical technical names")
    if len(set(value)) != len(value):
        raise ProtocolError("modules must be unique")
    return list(value)


def _precondition(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"registry_identity", "process_instance_id", "registry_epoch"}:
        raise ProtocolError("precondition is invalid")
    if not isinstance(value["registry_identity"], str) or not value["registry_identity"] or len(value["registry_identity"].encode("utf-8")) > 4096:
        raise ProtocolError("precondition is invalid")
    if not isinstance(value["process_instance_id"], str) or re.fullmatch(r"[0-9a-f]{32}", value["process_instance_id"]) is None:
        raise ProtocolError("precondition is invalid")
    if not isinstance(value["registry_epoch"], int) or isinstance(value["registry_epoch"], bool) or value["registry_epoch"] < 0:
        raise ProtocolError("precondition is invalid")
    return dict(value)


def validate_request(request: object) -> dict[str, Any]:
    """Validate and return a copy of one closed operation request."""
    value = _require_common(request)
    operation = value["operation"]
    if operation in _LIFECYCLE:
        if "precondition" not in value:
            raise ProtocolError("precondition is required")
        _precondition(value["precondition"])
        expected = f"{_LIFECYCLE[operation]} {value['client']}"
        if value.get("confirmation") != expected:
            raise ProtocolError("confirmation does not match operation and client")
        if "mode" in value:
            if operation == "lifecycle.stop" or value["mode"] not in _MODES:
                raise ProtocolError("mode is not valid for this lifecycle operation")
        if operation == "lifecycle.restart":
            allowed = _COMMON_KEYS | {"confirmation"}
            if "precondition" in value:
                _precondition(value["precondition"])
                allowed.add("precondition")
            if "mode" in value: allowed.add("mode")
            if set(value) == allowed:
                return value
            if set(value) == allowed | {"modules"}:
                value["modules"] = _modules(value["modules"])
                return value
            if set(value) == allowed | {"update_all"} and value["update_all"] is True:
                return value
            raise ProtocolError("restart request has invalid fields")
        allowed = _COMMON_KEYS | {"confirmation"}
        if "precondition" in value:
            _precondition(value["precondition"])
            allowed.add("precondition")
        if "mode" in value: allowed.add("mode")
        if set(value) != allowed:
            raise ProtocolError("lifecycle request has invalid fields")
        return value
    if operation == "updates.plan":
        if set(value) == _COMMON_KEYS | {"modules"}:
            value["modules"] = _modules(value["modules"])
            return value
        if set(value) == _COMMON_KEYS | {"update_all"} and value["update_all"] is True:
            return value
        raise ProtocolError("update plan must contain modules or update_all")
    if operation == "updates.apply":
        if "precondition" not in value:
            raise ProtocolError("precondition is required")
        _precondition(value["precondition"])
        if set(value) == _COMMON_KEYS | {"modules", "confirmation", "precondition"}:
            value["modules"] = _modules(value["modules"])
            if not isinstance(value["confirmation"], str) or not value["confirmation"]:
                raise ProtocolError("confirmation is required")
            return value
        if set(value) == _COMMON_KEYS | {"update_all", "confirmation", "precondition"} and value["update_all"] is True:
            if not isinstance(value["confirmation"], str) or not value["confirmation"]:
                raise ProtocolError("confirmation is required")
            return value
        raise ProtocolError("update apply has invalid fields")
    if operation == "runtime.reconcile":
        if set(value) != _COMMON_KEYS:
            raise ProtocolError("reconcile request has invalid fields")
        return value
    raise ProtocolError("operation is not allowlisted")


def _success(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"protocol_version": _PROTOCOL_VERSION, "operation": operation, "ok": True, "data": data}


def _failure(operation: str | None, code: str) -> dict[str, Any]:
    messages = {
        "INVALID_REQUEST": "invalid operation request",
        "CONFIRMATION_REQUIRED": "confirmation is required",
        "INSTANCE_NOT_FOUND": "requested instance was not found",
        "PRECONDITION_FAILED": "operation precondition was not met",
        "OPERATION_FAILED": "operation did not complete successfully",
        "READBACK_MISMATCH": "operation precondition was not met",
    }
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "operation": operation,
        "ok": False,
        "error": {"code": code, "message": messages.get(code, "operation failed")},
    }


def _default_context(request: dict[str, Any]):
    from types import SimpleNamespace
    from odoo_tui.cli import _controller, _selection
    from odoo_tui.commands import build_pm2_identity_spec
    from odoo_tui.config import ToolConfig

    config = ToolConfig.load(Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/config/odoo-tui.yaml"))
    selection = _selection(config, request["client"], request["environment"])
    controller, pm2, postgres = _controller(config, selection)
    return SimpleNamespace(
        config=config,
        selection=selection,
        client_spec=build_pm2_identity_spec(selection, "Client"),
        manager_spec=build_pm2_identity_spec(selection, "Database Manager"),
        controller=controller,
        pm2=pm2,
        postgres=postgres,
    )


def _client_preflight(context: Any, operation: str, mode: str = "Client") -> bool:
    """Verify the requested allowlisted PM2 identity before mutation."""
    target_spec = context.manager_spec if mode == "Database Manager" else context.client_spec
    try:
        app = context.pm2.assert_contract(target_spec)
    except Exception:
        app = None
        if operation == "lifecycle.start":
            try:
                from odoo_tui.commands import build_normal_start_spec
                build_normal_start_spec(context.selection, mode)
            except Exception:
                return False
            return True
        if operation == "lifecycle.restart":
            for fallback in (context.client_spec, context.manager_spec):
                try:
                    if context.pm2.assert_contract(fallback) is not None:
                        return True
                except Exception:
                    continue
            return False
        return False
    return app is not None or operation == "lifecycle.start"


def _execute_restart_with_update(request: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        plan = _fresh_update_plan(request, context)
    except ProtocolError:
        return _failure(request["operation"], "PRECONDITION_FAILED")
    try:
        verifier = getattr(context.controller, "log_verifier", None)
        if verifier is not None:
            context.controller.log_verifier = _UpdateLogVerifier(verifier)
        result = context.controller.execute_update(plan, plan.confirmation)
    except Exception:
        return _failure(request["operation"], "OPERATION_FAILED")
    if getattr(result, "verdict", None) != "PASS":
        return _failure(request["operation"], "OPERATION_FAILED")
    try:
        observed = context.pm2.status(context.selection.pm2_process)
        state = str(getattr(observed, "status", "")).lower() if observed is not None else "stopped"
    except Exception:
        return _failure(request["operation"], "READBACK_MISMATCH")
    if state != "online":
        return _failure(request["operation"], "READBACK_MISMATCH")
    return _success(request["operation"], {
        "client": request["client"],
        "state": state,
        "control_mode": "client",
        "modules": [str(name) for name in getattr(plan, "modules", ())],
    })


def _execute_lifecycle(request: dict[str, Any], context: Any) -> dict[str, Any]:
    operation = request["operation"]
    if operation == "lifecycle.restart" and ("modules" in request or request.get("update_all") is True):
        if request.get("mode", "client") != "client":
            return _failure(operation, "PRECONDITION_FAILED")
        return _execute_restart_with_update(request, context)
    mode = _MODES.get(request.get("mode", "client"), "Client")
    if not _client_preflight(context, operation, mode):
        return _failure(operation, "PRECONDITION_FAILED")
    try:
        allow_concurrent = not bool(context.config.single_active_instance)
        if operation == "lifecycle.start":
            result = context.controller.start(context.selection, mode, allow_concurrent=allow_concurrent)
            expected = "online"
        elif operation == "lifecycle.stop":
            result = context.controller.stop(context.selection, mode)
            expected = "stopped"
        else:
            result = context.controller.restart(context.selection, mode, allow_concurrent=allow_concurrent)
            expected = "online"
    except Exception:
        return _failure(operation, "OPERATION_FAILED")
    if getattr(result, "verdict", None) != "PASS":
        return _failure(operation, "OPERATION_FAILED")
    try:
        observed = context.pm2.status(context.selection.pm2_process)
        state = str(getattr(observed, "status", "")).lower() if observed is not None else "stopped"
    except Exception:
        return _failure(operation, "READBACK_MISMATCH")
    if state != expected:
        return _failure(operation, "READBACK_MISMATCH")
    return _success(operation, {"state": state, "control_mode": "database_manager" if mode == "Database Manager" else "client"})


def _catalog(context: Any):
    catalog = getattr(context, "catalog", None)
    if catalog is not None:
        return catalog
    from odoo_tui.modules import ModuleCatalog
    return ModuleCatalog.from_config(context.selection.config_path, version=context.selection.release.version)


def _fresh_update_plan(request: dict[str, Any], context: Any) -> Any:
    if not _client_preflight(context, request["operation"]):
        raise ProtocolError("client identity precondition failed")
    database = getattr(context.selection, "database_hint", None)
    if not isinstance(database, str) or not database:
        raise ProtocolError("client database is unavailable")
    update_all = request.get("update_all") is True
    modules = [] if update_all else request["modules"]
    module_input = "" if update_all else ",".join(modules)
    try:
        plan = context.controller.plan_update(
            context.selection,
            database,
            module_input,
            _catalog(context),
            database_mode="Client",
            update_all=update_all,
        )
    except Exception as exc:
        raise ProtocolError("controller could not create an update plan") from exc
    names = tuple(getattr(plan, "modules", ()))
    if not names:
        raise ProtocolError("update plan is empty")
    return plan


def _execute_update(request: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        plan = _fresh_update_plan(request, context)
    except ProtocolError:
        return _failure(request["operation"], "PRECONDITION_FAILED")
    kind = "update_all" if request.get("update_all") is True else "selected"
    names = [str(name) for name in getattr(plan, "modules", ())]
    if request["operation"] == "updates.plan":
        return _success(request["operation"], {
            "client": request["client"],
            "kind": kind,
            "database": str(plan.database),
            "modules": names,
            "confirmation": str(plan.confirmation),
        })
    if request["confirmation"] != plan.confirmation:
        return _failure(request["operation"], "CONFIRMATION_REQUIRED")
    try:
        result = context.controller.execute_update(plan, request["confirmation"])
    except Exception:
        return _failure(request["operation"], "OPERATION_FAILED")
    if getattr(result, "verdict", None) != "PASS":
        return _failure(request["operation"], "OPERATION_FAILED")
    try:
        observed = context.pm2.status(context.selection.pm2_process)
        state = str(getattr(observed, "status", "")).lower() if observed is not None else "stopped"
    except Exception:
        return _failure(request["operation"], "READBACK_MISMATCH")
    if state != "online":
        return _failure(request["operation"], "READBACK_MISMATCH")
    return _success(request["operation"], {"client": request["client"], "kind": kind, "modules": names, "state": state})


def _reconcile(request: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        observed = context.pm2.status(context.selection.pm2_process)
        state = str(getattr(observed, "status", "unknown")).lower() if observed is not None else "stopped"
        if state not in {"online", "stopped", "errored", "unknown"}:
            state = "unknown"
    except Exception:
        state = "unknown"
    mode = "unknown"
    try:
        if context.pm2.assert_contract(context.client_spec) is not None:
            mode = "client"
        elif context.pm2.assert_contract(context.manager_spec) is not None:
            mode = "database_manager"
    except Exception:
        mode = "unknown"
    database_exists: bool | None = None
    database = getattr(context.selection, "database_hint", None)
    if isinstance(database, str) and database:
        try:
            database_exists = bool(context.postgres.database_exists(database))
        except Exception:
            database_exists = None
    return _success(request["operation"], {"state": state, "control_mode": mode, "database_exists": database_exists})


def execute(request: object, *, context_factory: Any = _default_context) -> dict[str, Any]:
    try:
        validated = validate_request(request)
    except ProtocolError:
        operation = request.get("operation") if isinstance(request, dict) and isinstance(request.get("operation"), str) else None
        return _failure(operation, "INVALID_REQUEST")
    operation = validated["operation"]
    try:
        context = context_factory(validated)
    except KeyError:
        return _failure(operation, "INSTANCE_NOT_FOUND")
    except Exception:
        return _failure(operation, "OPERATION_FAILED")
    if operation == "runtime.reconcile":
        return _reconcile(validated, context)
    if operation in {"lifecycle.start", "lifecycle.stop", "lifecycle.restart"}:
        return _execute_lifecycle(validated, context)
    if operation in {"updates.plan", "updates.apply"}:
        return _execute_update(validated, context)
    return _failure(operation, "INVALID_REQUEST")


def main() -> int:
    import json
    import sys

    try:
        request = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, UnicodeDecodeError):
        request = None
    print(json.dumps(execute(request), separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
