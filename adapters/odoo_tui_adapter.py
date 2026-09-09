"""Strict typed read-only contract for the plugin-to-odoo-tui boundary."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from worker_bridge import BridgeError, OdooTuiWorkerBridge, WorkerConfig

class ReadOnlyTransport(Protocol):
    def request(self, operation: str, **params: str) -> str | bytes | Mapping[str, Any]: ...

class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 503):
        super().__init__(message); self.code, self.message, self.status_code = code, message, status_code
    def as_dict(self): return {"error": {"code": self.code, "message": self.message}}

@dataclass(frozen=True)
class ClientRecord: name: str; release: str | None = None; environment: str | None = None; local_url: str | None = None
@dataclass(frozen=True)
class ReleaseRecord: version: str
@dataclass(frozen=True)
class InstanceIdentity:
    client: str; release: str; environment: str; config_identity: str | None = None; database: str | None = None; http_port: int | None = None; longpolling_port: int | None = None
@dataclass(frozen=True)
class RuntimeStatus:
    state: str; pid: int | None = None; process_name: str | None = None; pm2_id: int | None = None; startup_mode: str | None = None
@dataclass(frozen=True)
class RuntimeControl:
    control_mode: str
    lifecycle_eligible: bool
    reason: str | None = None
@dataclass(frozen=True)
class ModuleRecord:
    name: str; version: str | None = None; installed: bool = False; installable: bool | None = None; update_available: bool = False; dependencies: tuple[str, ...] = ()
@dataclass(frozen=True)
class DatabaseRecord: name: str; exists: bool = True


def _malformed(operation: str, detail: str = "response schema is invalid") -> AdapterError:
    return AdapterError("MALFORMED_RESPONSE", f"The {operation} response {detail}.")

def _payload(raw: str | bytes | Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    try: value = raw if isinstance(raw, Mapping) else json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _malformed(operation, "was not valid JSON") from exc
    if not isinstance(value, Mapping): raise _malformed(operation, "must be a JSON object")
    if "error" in value:
        if set(value) != {"error"}:
            raise _malformed(operation, "contains extra top-level fields")
        error = value["error"]
        if isinstance(error, Mapping) and set(error) == {"code", "message"} and isinstance(error.get("code"), str) and isinstance(error.get("message"), str):
            safe = {
                "STATUS_PROBE_FAILED": "odoo-tui status probe failed",
                "DEPENDENCY_UNAVAILABLE": "odoo-tui package is unavailable",
                "CONFIGURATION_UNAVAILABLE": "odoo-tui configuration is unavailable",
                "INSTANCE_NOT_FOUND": "the requested instance was not found",
                "STALE_INSTANCE_IDENTITY": "the instance identity is stale",
            }.get(error["code"], "odoo-tui worker reported an error")
            raise AdapterError(error["code"], safe, status_code=404 if error["code"] in {"INSTANCE_NOT_FOUND", "STALE_INSTANCE_IDENTITY"} else 503)
        raise _malformed(operation, "contains an invalid error")
    return value

def _keys(value: Mapping[str, Any], expected: set[str], operation: str) -> None:
    if set(value) != expected: raise _malformed(operation, "has missing or unexpected fields")
def _list(payload: Mapping[str, Any], key: str, operation: str) -> list[Any]:
    _keys(payload, {key}, operation)
    if not isinstance(payload[key], list): raise _malformed(operation, f"field '{key}' is invalid")
    return payload[key]
def _text(value: Any, field: str, operation: str, nullable=False) -> str | None:
    if nullable and value is None: return None
    if not isinstance(value, str) or not value.strip(): raise _malformed(operation, f"field '{field}' is invalid")
    return value
def _int(value: Any, field: str, operation: str) -> int | None:
    if value is None: return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0: raise _malformed(operation, f"field '{field}' is invalid")
    return value
def _bool(value: Any, field: str, operation: str, nullable=False) -> bool | None:
    if nullable and value is None: return None
    if not isinstance(value, bool): raise _malformed(operation, f"field '{field}' is invalid")
    return value

class OdooTuiAdapter:
    def __init__(self, transport: ReadOnlyTransport | None = None, *, worker_config: WorkerConfig | None = None):
        if transport is not None and worker_config is not None: raise ValueError("transport and worker_config are mutually exclusive")
        self._transport = transport or OdooTuiWorkerBridge(worker_config or WorkerConfig())
    def _request(self, operation: str, **params: str) -> Mapping[str, Any]:
        try: return _payload(self._transport.request(operation, **params), operation)
        except BridgeError as exc:
            status = 400 if exc.code in {"INVALID_REQUEST", "OPERATION_NOT_ALLOWED"} else 404 if exc.code in {"INSTANCE_NOT_FOUND", "STALE_INSTANCE_IDENTITY"} else 503
            raise AdapterError(exc.code, exc.message, status_code=status) from exc
        except AdapterError: raise
        except Exception as exc: raise AdapterError("DEPENDENCY_UNAVAILABLE", f"The {operation} read is unavailable.") from exc
    def list_clients(self):
        op="clients.list"; result=[]
        for item in _list(self._request(op), "clients", op):
            if not isinstance(item, Mapping): raise _malformed(op)
            _keys(item, {"name","release","environment","local_url"}, op)
            result.append(ClientRecord(_text(item["name"],"name",op), _text(item["release"],"release",op,True), _text(item["environment"],"environment",op,True), _text(item["local_url"],"local_url",op,True)))
        return tuple(sorted(result,key=lambda x:x.name))
    def list_releases(self):
        op="releases.list"; result=[]
        for item in _list(self._request(op), "releases", op):
            if not isinstance(item, Mapping): raise _malformed(op)
            _keys(item,{"version"},op); result.append(ReleaseRecord(_text(item["version"],"version",op)))
        return tuple(sorted(result,key=lambda x:x.version))
    def get_instance(self, client: str):
        op="instance.identity"; p=self._request(op,client=client); _keys(p,{"instance"},op); x=p["instance"]
        if not isinstance(x,Mapping): raise AdapterError("STALE_INSTANCE_IDENTITY","The selected instance identity is missing or stale.",status_code=404)
        _keys(x,{"client","release","environment","config_identity","database","http_port","longpolling_port"},op)
        return InstanceIdentity(_text(x["client"],"client",op),_text(x["release"],"release",op),_text(x["environment"],"environment",op),_text(x["config_identity"],"config_identity",op,True),_text(x["database"],"database",op,True),_int(x["http_port"],"http_port",op),_int(x["longpolling_port"],"longpolling_port",op))
    def get_status(self, client: str):
        op="runtime.status"; p=self._request(op,client=client); _keys(p,{"status"},op); x=p["status"]
        if not isinstance(x,Mapping): raise AdapterError("STATUS_PROBE_FAILED","Runtime status is unavailable.")
        _keys(x,{"state","pid","process_name","pm2_id","startup_mode"},op)
        if x["state"] not in {"online","stopped","errored","unknown"}: raise _malformed(op)
        return RuntimeStatus(_text(x["state"],"state",op),_int(x["pid"],"pid",op),_text(x["process_name"],"process_name",op,True),_int(x["pm2_id"],"pm2_id",op),_text(x["startup_mode"],"startup_mode",op,True))
    def get_control(self, client: str):
        op = "runtime.control"
        payload = self._request(op, client=client)
        _keys(payload, {"control"}, op)
        value = payload["control"]
        if not isinstance(value, Mapping):
            raise _malformed(op)
        _keys(value, {"control_mode", "lifecycle_eligible", "reason"}, op)
        mode = value["control_mode"]
        if mode not in {"client", "database_manager", "unknown"}:
            raise _malformed(op, "field 'control_mode' is invalid")
        eligible = _bool(value["lifecycle_eligible"], "lifecycle_eligible", op)
        if not isinstance(eligible, bool):
            raise _malformed(op, "field 'lifecycle_eligible' is invalid")
        reason = _text(value["reason"], "reason", op, True)
        if mode == "client" and (not eligible or reason is not None):
            raise _malformed(op, "client control state is invalid")
        if mode == "database_manager" and (eligible or reason != "Database Manager mode is not controllable here"):
            raise _malformed(op, "database manager control state is invalid")
        if mode == "unknown" and (eligible or reason != "Runtime identity could not be verified"):
            raise _malformed(op, "unknown control state is invalid")
        return RuntimeControl(mode, eligible, reason)
    def list_modules(self, client: str):
        op="modules.list"; p=self._request(op,client=client); _keys(p,{"client","modules"},op); _text(p["client"],"client",op); result=[]
        modules = p["modules"]
        if not isinstance(modules, list): raise _malformed(op, "field 'modules' is invalid")
        for x in modules:
            if not isinstance(x,Mapping): raise _malformed(op)
            _keys(x,{"name","version","installed","installable","update_available","dependencies"},op)
            deps=x["dependencies"]
            if not isinstance(deps,list) or any(not isinstance(d,str) or not d for d in deps): raise _malformed(op)
            result.append(ModuleRecord(_text(x["name"],"name",op),_text(x["version"],"version",op,True),_bool(x["installed"],"installed",op),_bool(x["installable"],"installable",op,True),_bool(x["update_available"],"update_available",op),tuple(sorted(deps))))
        return tuple(sorted(result,key=lambda x:x.name))
    def list_databases(self, client: str):
        op="databases.list"; p=self._request(op,client=client); _keys(p,{"client","databases"},op); _text(p["client"],"client",op); result=[]
        if not isinstance(p["databases"],list): raise _malformed(op)
        for x in p["databases"]:
            if not isinstance(x,Mapping): raise _malformed(op)
            _keys(x,{"name","exists"},op); result.append(DatabaseRecord(_text(x["name"],"name",op),_bool(x["exists"],"exists",op)))
        return tuple(sorted(result,key=lambda x:x.name))
