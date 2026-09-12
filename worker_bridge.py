"""Fixed-interpreter JSON bridge for the seven read-only worker operations."""
from __future__ import annotations

import json
import os
import re
import subprocess
import selectors
import signal
import time
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
_MAX_CLIENT_BYTES = 512

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
    def _kill_process_group(process: subprocess.Popen[bytes], process_group_id: int | None = None) -> None:
        group_id = process_group_id if process_group_id is not None else process.pid
        try:
            os.killpg(group_id, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            if process.poll() is None:
                process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _bounded_io(process: subprocess.Popen[bytes], payload: bytes, timeout: float, process_group_id: int | None = None) -> tuple[bytes, bytes]:
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        streams = (process.stdout, process.stderr)
        selector: selectors.BaseSelector | None = None
        output = [bytearray(), bytearray()]
        stream_index = {process.stdout: 0, process.stderr: 1}
        deadline = time.monotonic() + timeout
        try:
            selector = selectors.DefaultSelector()
            process.stdin.write(payload)
            process.stdin.close()
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                for key, _ in selector.select(remaining):
                    index = stream_index[key.fileobj]
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    output[index].extend(chunk)
                    if len(output[index]) > _MAX_OUTPUT_BYTES:
                        raise BridgeError("OUTPUT_TOO_LARGE", "odoo-tui worker output exceeded the protocol limit")
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
            return bytes(output[0]), bytes(output[1])
        except OSError as exc:
            OdooTuiWorkerBridge._kill_process_group(process, process_group_id)
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker communication failed") from exc
        except (ValueError, KeyError) as exc:
            OdooTuiWorkerBridge._kill_process_group(process, process_group_id)
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker communication failed") from exc
        except (BridgeError, subprocess.TimeoutExpired):
            OdooTuiWorkerBridge._kill_process_group(process, process_group_id)
            raise
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            for stream in streams:
                try:
                    stream.close()
                except OSError:
                    pass
            if selector is not None:
                selector.close()

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
        if isinstance(client, str) and len(client.encode("utf-8")) > _MAX_CLIENT_BYTES:
            raise BridgeError("INVALID_REQUEST", "client selector exceeds the protocol limit")
        return {"protocol_version": 1, "operation": operation, "client": "" if collection else client, "environment": "local"}

    def invoke(self, *, operation: str, client: str | None = None, environment: str = "local") -> dict[str, Any]:
        request = self._request(operation, client, environment)
        if not self.config.interpreter.is_file() or not os.access(self.config.interpreter, os.X_OK):
            raise BridgeError("DEPENDENCY_UNAVAILABLE", "configured worker interpreter is unavailable")
        if not self.config.worker.is_file():
            raise BridgeError("PROCESS_CRASH", "configured worker script is unavailable")
        env = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}
        process: subprocess.Popen[bytes] | None = None
        process_group_id: int | None = None
        try:
            process = subprocess.Popen([str(self.config.interpreter), str(self.config.worker)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False, start_new_session=True)
            process_group_id = process.pid
            stdout, stderr = self._bounded_io(process, (json.dumps(request, separators=(",", ":")) + "\n").encode(), self.config.timeout_seconds, process_group_id)
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                self._kill_process_group(process, process_group_id)
            raise BridgeError("TIMEOUT", "odoo-tui worker timed out") from exc
        except OSError as exc:
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker could not be started") from exc
        if len(stdout) > _MAX_OUTPUT_BYTES or len(stderr) > _MAX_OUTPUT_BYTES:
            raise BridgeError("OUTPUT_TOO_LARGE", "odoo-tui worker output exceeded the protocol limit")
        if process.returncode < 0 or process.returncode != 0:
            raise BridgeError("PROCESS_CRASH", "odoo-tui worker terminated unexpectedly")
        try:
            response = json.loads(stdout.decode("utf-8"))
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
