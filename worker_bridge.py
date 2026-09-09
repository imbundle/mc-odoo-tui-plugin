"""Fixed-interpreter JSON bridge for the six read-only worker operations."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROTOCOL_VERSION = 1
_ALLOWED_OPERATIONS = frozenset({
    "clients.list", "releases.list", "instance.identity", "runtime.status", "runtime.control",
    "modules.list", "databases.list",
})
_WORKER_ERROR_CODES = frozenset({
    "INVALID_REQUEST", "OPERATION_NOT_ALLOWED", "CONFIGURATION_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE", "INSTANCE_NOT_FOUND", "STALE_INSTANCE_IDENTITY",
    "MALFORMED_RESPONSE", "STATUS_PROBE_FAILED", "TIMEOUT", "PROCESS_CRASH",
})
_CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_DEFAULT_INTERPRETER = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/.venv/bin/python3")
_DEFAULT_WORKER = Path(__file__).with_name("odoo_tui_worker.py")
_DEFAULT_CONFIG = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/config/odoo-tui.yaml")
_MAX_OUTPUT_BYTES = 64 * 1024

_SAFE_MESSAGES = {
    "CONFIGURATION_UNAVAILABLE": "odoo-tui configuration is unavailable",
    "DEPENDENCY_UNAVAILABLE": "odoo-tui package is unavailable",
    "INSTANCE_NOT_FOUND": "the requested instance was not found",
    "STALE_INSTANCE_IDENTITY": "the instance identity is stale",
    "STATUS_PROBE_FAILED": "odoo-tui status probe failed",
}

class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message
    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

@dataclass(frozen=True)
class WorkerConfig:
    interpreter: Path = _DEFAULT_INTERPRETER
    worker: Path = _DEFAULT_WORKER
    config_path: Path = _DEFAULT_CONFIG
    timeout_seconds: float = 5.0
    def __post_init__(self) -> None:
        interpreter, worker, config = map(lambda p: Path(p).expanduser(), (self.interpreter, self.worker, self.config_path))
        if not interpreter.is_absolute() or not worker.is_absolute() or not config.is_absolute():
            raise ValueError("worker paths must be absolute")
        if self.timeout_seconds <= 0:
            raise ValueError("worker timeout must be positive")
        object.__setattr__(self, "interpreter", interpreter)
        object.__setattr__(self, "worker", worker)
        object.__setattr__(self, "config_path", config)

class OdooTuiWorkerBridge:
    def __init__(self, config: WorkerConfig):
        self.config = config

    @staticmethod
    def _request(operation: str, client: str | None, environment: str) -> dict[str, Any]:
        if operation not in _ALLOWED_OPERATIONS:
            raise BridgeError("OPERATION_NOT_ALLOWED", "operation is not allowlisted")
        if environment != "local":
            raise BridgeError("INVALID_REQUEST", "environment must be local")
        collection = operation in {"clients.list", "releases.list"}
        if collection and client not in (None, ""):
            raise BridgeError("INVALID_REQUEST", "collection reads require an empty client selector")
        if not collection and (not isinstance(client, str) or not _CLIENT_RE.fullmatch(client)):
            raise BridgeError("INVALID_REQUEST", "client is not a valid registered identifier")
        return {"protocol_version": 1, "operation": operation, "client": "" if collection else client, "environment": "local"}

    def invoke(self, *, operation: str, client: str | None = None, environment: str = "local") -> dict[str, Any]:
        request = self._request(operation, client, environment)
        if not self.config.interpreter.is_file() or not os.access(self.config.interpreter, os.X_OK):
            raise BridgeError("DEPENDENCY_UNAVAILABLE", "configured worker interpreter is unavailable")
        if not self.config.worker.is_file():
            raise BridgeError("PROCESS_CRASH", "configured worker script is unavailable")
        env = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen([str(self.config.interpreter), str(self.config.worker)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env, shell=False)
            stdout, stderr = process.communicate(json.dumps(request, separators=(",", ":")) + "\n", timeout=self.config.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                process.kill(); process.communicate()
            raise BridgeError("TIMEOUT", "odoo-tui worker timed out") from exc
        except OSError as exc:
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker could not be started") from exc
        if len(stdout.encode()) > _MAX_OUTPUT_BYTES or len(stderr.encode()) > _MAX_OUTPUT_BYTES:
            raise BridgeError("MALFORMED_RESPONSE", "odoo-tui worker output exceeded the protocol limit")
        if process.returncode < 0 or process.returncode != 0:
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker terminated unexpectedly")
        try:
            response = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BridgeError("MALFORMED_RESPONSE", "odoo-tui worker returned invalid JSON") from exc
        if not isinstance(response, dict) or set(response) != {"protocol_version", "operation", "ok", "data"} and set(response) != {"protocol_version", "operation", "ok", "error"}:
            raise BridgeError("MALFORMED_RESPONSE", "odoo-tui worker response has an invalid schema")
        if response.get("protocol_version") != 1 or response.get("operation") != operation:
            raise BridgeError("MALFORMED_RESPONSE", "odoo-tui worker response does not match the request")
        if response.get("ok") is True and isinstance(response.get("data"), dict):
            return response
        error = response.get("error")
        if response.get("ok") is False and isinstance(error, dict) and set(error) == {"code", "message"}:
            code, message = error["code"], error["message"]
            if isinstance(code, str) and code in _WORKER_ERROR_CODES and isinstance(message, str):
                raise BridgeError(code, _SAFE_MESSAGES.get(code, "odoo-tui worker reported an error"))
        raise BridgeError("MALFORMED_RESPONSE", "odoo-tui worker response has an invalid schema")

    def request(self, operation: str, **params: str) -> dict[str, Any]:
        allowed = {"client", "environment"}
        if set(params) - allowed:
            raise BridgeError("INVALID_REQUEST", "request contains unsupported fields")
        client = params.get("client", "")
        response = self.invoke(operation=operation, client=client, environment=params.get("environment", "local"))
        return response["data"]
