"""Read-only worker executed by the fixed odoo-tui interpreter."""
from __future__ import annotations
import ast, json, re, sys
from pathlib import Path
from typing import Any

_PROTOCOL_VERSION = 1
_CONFIG_PATH = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/config/odoo-tui.yaml")
_ALLOWED_OPERATIONS = frozenset({"clients.list", "releases.list", "instance.identity", "runtime.status", "runtime.control", "modules.list", "databases.list"})
_CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _manifest_display_name(manifest: Path, technical_name: str) -> str:
    try:
        data = ast.literal_eval(manifest.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return technical_name
    if not isinstance(data, dict):
        return technical_name
    display_name = data.get("name")
    return display_name.strip()[:160] if isinstance(display_name, str) and display_name.strip() else technical_name


def _error(operation: str | None, code: str, message: str) -> dict[str, Any]:
    return {"protocol_version": 1, "operation": operation, "ok": False, "error": {"code": code, "message": message}}


def _load(request: dict[str, Any]):
    try:
        from odoo_tui.cli import _selection
        from odoo_tui.config import ToolConfig
        from odoo_tui.modules import ModuleCatalog
        from odoo_tui.pm2 import PM2Client
        from odoo_tui.postgres import PostgresClient
        from odoo_tui.registry import Registry
        from odoo_tui.runner import SubprocessRunner
    except ImportError:
        return None, _error(request.get("operation"), "DEPENDENCY_UNAVAILABLE", "odoo-tui package is unavailable")
    try:
        config = ToolConfig.load(_CONFIG_PATH)
        registry = Registry(config.release_dir, config.client_dir, runtime_root=config.runtime_root)
        runner = SubprocessRunner()
        pm2 = PM2Client(config.pm2_binary, config.pm2_home, runner, runtime_user=config.runtime_user)
        postgres = PostgresClient(runner, host=config.postgres_host, port=config.postgres_port, user=config.postgres_user)
    except Exception:
        return None, _error(request.get("operation"), "CONFIGURATION_UNAVAILABLE", "odoo-tui configuration is unavailable")
    try:
        selection = _selection(config, request["client"], "local") if request["client"] else None
    except Exception:
        return None, _error(request.get("operation"), "INSTANCE_NOT_FOUND", "the requested instance was not found")
    return (config, registry, pm2, postgres, ModuleCatalog, selection, _selection), None


def _read(request: dict[str, Any]) -> dict[str, Any]:
    loaded, error = _load(request)
    if error: return error
    config, registry, pm2, postgres, module_catalog, selection, selection_fn = loaded
    operation = request["operation"]
    try:
        if operation == "clients.list":
            releases = set(registry.release_versions())
            clients = []
            for name in registry.client_names():
                selected = selection_fn(config, name, "local")
                if selected.release.version not in releases:
                    raise ValueError("manifest release is not registered")
                clients.append({"name": selected.client.name, "release": selected.client.version, "environment": "local", "local_url": None})
            data = {"clients": clients}
        elif operation == "releases.list":
            data = {"releases": [{"version": version} for version in registry.release_versions()]}
        elif operation == "instance.identity":
            if selection is None: raise LookupError
            identity = {"client": selection.client.name, "release": selection.client.version, "environment": selection.environment,
                "config_identity": str(selection.config_path), "database": selection.database_hint,
                "http_port": selection.http_port, "longpolling_port": selection.gevent_port}
            if identity["client"] != request["client"] or identity["environment"] != "local":
                return _error(operation, "STALE_INSTANCE_IDENTITY", "the instance identity is stale")
            data = {"instance": identity}
        elif operation == "runtime.control":
            if selection is None:
                raise LookupError
            from odoo_tui.commands import build_normal_start_spec, build_pm2_identity_spec

            client_spec = build_pm2_identity_spec(selection, "Client")
            manager_spec = build_pm2_identity_spec(selection, "Database Manager")
            app = pm2.status(selection.pm2_process)
            if app is None:
                try:
                    build_normal_start_spec(selection, "Client")
                except Exception:
                    data = {"control": {"control_mode": "unknown", "lifecycle_eligible": False,
                                         "reason": "Runtime identity could not be verified"}}
                else:
                    data = {"control": {"control_mode": "client", "lifecycle_eligible": True,
                                         "reason": None}}
            else:
                try:
                    pm2.assert_contract(client_spec)
                except Exception:
                    try:
                        pm2.assert_contract(manager_spec)
                    except Exception:
                        data = {"control": {"control_mode": "unknown", "lifecycle_eligible": False,
                                             "reason": "Runtime identity could not be verified"}}
                    else:
                        data = {"control": {"control_mode": "database_manager", "lifecycle_eligible": False,
                                             "reason": "Database Manager mode is not controllable here"}}
                else:
                    data = {"control": {"control_mode": "client", "lifecycle_eligible": True,
                                         "reason": None}}
        elif operation == "runtime.status":
            if selection is None: raise LookupError
            app = pm2.status(selection.pm2_process)
            data = {"status": {"state": "stopped" if app is None else str(app.status).lower(),
                "pid": None if app is None else app.pid, "process_name": selection.pm2_process,
                "pm2_id": None if app is None else app.pm_id, "startup_mode": None}}
        elif operation == "modules.list":
            if selection is None: raise LookupError
            catalog = module_catalog.from_config(selection.config_path, version=selection.release.version)
            client_root = selection.client_root
            client_modules = {name: info for name, info in catalog.modules.items() if info.manifest.resolve().is_relative_to(client_root)}
            # Exactly one module-state read for the selected client.
            states = postgres.module_states(selection.database_hint, tuple(client_modules)) if selection.database_hint else {}
            data = {"client": selection.client.name, "modules": [{"name": info.name, "display_name": _manifest_display_name(info.manifest, info.name), "version": info.version,
                "installed": states.get(name) == "installed", "installable": info.installable,
                "update_available": False, "dependencies": list(info.depends)} for name, info in sorted(client_modules.items())]}
        else:
            if selection is None: raise LookupError
            names = set(postgres.list_databases()) if selection.database_hint else set()
            data = {"client": selection.client.name, "databases": ([{"name": selection.database_hint, "exists": selection.database_hint in names}] if selection.database_hint else [])}
    except LookupError:
        return _error(operation, "INSTANCE_NOT_FOUND", "the requested instance was not found")
    except Exception:
        return _error(operation, "STATUS_PROBE_FAILED" if operation in {"runtime.status", "modules.list", "databases.list"} else "MALFORMED_RESPONSE", "odoo-tui read probe failed")
    return {"protocol_version": 1, "operation": operation, "ok": True, "data": data}


def handle(request: object) -> dict[str, Any]:
    if not isinstance(request, dict): return _error(None, "INVALID_REQUEST", "request must be an object")
    operation = request.get("operation")
    if not isinstance(operation, str): return _error(None, "INVALID_REQUEST", "operation is required")
    if operation not in _ALLOWED_OPERATIONS: return _error(operation, "OPERATION_NOT_ALLOWED", "operation is not allowlisted")
    if set(request) != {"protocol_version", "operation", "client", "environment"}: return _error(operation, "INVALID_REQUEST", "request contains unsupported fields")
    if request["protocol_version"] != 1 or isinstance(request["protocol_version"], bool): return _error(operation, "INVALID_REQUEST", "unsupported protocol version")
    if request["environment"] != "local": return _error(operation, "INVALID_REQUEST", "environment must be local")
    collection = operation in {"clients.list", "releases.list"}
    if collection and request["client"] != "": return _error(operation, "INVALID_REQUEST", "collection reads require an empty client selector")
    if not collection and (not isinstance(request["client"], str) or not _CLIENT_RE.fullmatch(request["client"])): return _error(operation, "INVALID_REQUEST", "client is not a valid registered identifier")
    return _read(request)


def main() -> int:
    try: request = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, UnicodeDecodeError): request = None
    print(json.dumps(handle(request), separators=(",", ":")), flush=True)
    return 0

if __name__ == "__main__": raise SystemExit(main())
