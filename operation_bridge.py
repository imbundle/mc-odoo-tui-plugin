"""Bounded subprocess bridge for plugin-owned Odoo mutations."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operation_worker import ProtocolError, validate_request

_DEFAULT_INTERPRETER = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/.venv/bin/python3")
_DEFAULT_WORKER = Path(__file__).with_name("operation_worker.py")
_DEFAULT_CONFIG = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/config/odoo-tui.yaml")
_MAX_REQUEST_BYTES = 16 * 1024
_MAX_STDOUT_BYTES = 64 * 1024
_MAX_STDERR_BYTES = 8 * 1024


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OperationBridgeConfig:
    interpreter: Path = _DEFAULT_INTERPRETER
    worker: Path = _DEFAULT_WORKER
    config_path: Path = _DEFAULT_CONFIG
    lifecycle_timeout_seconds: float = 120.0
    update_timeout_seconds: float = 900.0
    reconcile_timeout_seconds: float = 15.0
    term_grace_seconds: float = 5.0
    reap_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        for field in ("interpreter", "worker", "config_path"):
            value = Path(getattr(self, field)).expanduser()
            if not value.is_absolute():
                raise ValueError("operation worker paths must be absolute")
            object.__setattr__(self, field, value)
        for field in (
            "lifecycle_timeout_seconds",
            "update_timeout_seconds",
            "reconcile_timeout_seconds",
            "term_grace_seconds",
            "reap_timeout_seconds",
        ):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} must be positive")


_SAFE_MESSAGES = {
    "INVALID_REQUEST": "invalid operation request",
    "CONFIRMATION_REQUIRED": "confirmation is required",
    "INSTANCE_NOT_FOUND": "requested instance was not found",
    "PRECONDITION_FAILED": "operation precondition was not met",
    "OPERATION_FAILED": "operation did not complete successfully",
    "READBACK_MISMATCH": "operation precondition was not met",
}
_WORKER_CODES = frozenset(_SAFE_MESSAGES)


class OperationBridge:
    def __init__(self, config: OperationBridgeConfig):
        self.config = config
        self._mutex = __import__("threading").Lock()

    def _timeout_for(self, operation: str) -> float:
        return self.config.update_timeout_seconds if operation.startswith("updates.") else self.config.lifecycle_timeout_seconds

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes], config: OperationBridgeConfig) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=config.term_grace_seconds)
            except subprocess.TimeoutExpired:
                pass
        # The leader may have exited while descendants remain in the session.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=config.reap_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=config.reap_timeout_seconds)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _close_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _collect(self, process: subprocess.Popen[bytes], payload: bytes, timeout: float) -> tuple[bytes, bytes]:
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(payload)
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout = bytearray()
        stderr = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_group(process, self.config)
                    raise BridgeError("OPERATION_TIMED_OUT", "operation outcome is indeterminate; refresh status")
                for key, _ in selector.select(min(0.1, remaining)):
                    stream = key.fileobj
                    chunk = os.read(stream.fileno(), 8192)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    target = stdout if key.data == "stdout" else stderr
                    limit = _MAX_STDOUT_BYTES if key.data == "stdout" else _MAX_STDERR_BYTES
                    target.extend(chunk)
                    if len(target) > limit:
                        self._terminate_group(process, self.config)
                        raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker output exceeded the protocol limit")
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._terminate_group(process, self.config)
                raise BridgeError("OPERATION_TIMED_OUT", "operation outcome is indeterminate; refresh status")
            return bytes(stdout), bytes(stderr)
        finally:
            selector.close()
            self._close_streams(process)

    @staticmethod
    def _decode_response(raw: bytes, operation: str) -> dict[str, Any]:
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker response has an invalid schema")
        if response.get("protocol_version") != 1 or response.get("operation") != operation:
            raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker response does not match the request")
        if response.get("ok") is True and set(response) == {"protocol_version", "operation", "ok", "data"} and isinstance(response["data"], dict):
            return response
        if response.get("ok") is False and set(response) == {"protocol_version", "operation", "ok", "error"}:
            error = response["error"]
            if isinstance(error, dict) and set(error) == {"code", "message"} and error.get("code") in _WORKER_CODES and isinstance(error.get("message"), str):
                code = error["code"]
                raise BridgeError(code, _SAFE_MESSAGES[code])
        raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker response has an invalid schema")

    def _run_process(self, payload: bytes, operation: str, timeout: float) -> bytes:
        if not self.config.interpreter.is_file() or not os.access(self.config.interpreter, os.X_OK):
            raise BridgeError("OPERATION_UNAVAILABLE", "operation bridge is unavailable")
        if not self.config.worker.is_file():
            raise BridgeError("OPERATION_UNAVAILABLE", "operation bridge is unavailable")
        environment = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}
        try:
            process = subprocess.Popen(
                [str(self.config.interpreter), str(self.config.worker)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise BridgeError("OPERATION_UNAVAILABLE", "operation bridge is unavailable") from exc
        stdout, _ = self._collect(process, payload, timeout)
        if process.returncode != 0:
            raise BridgeError("WORKER_PROTOCOL_ERROR", "operation worker terminated unexpectedly")
        return stdout

    def _reconcile(self, client: str) -> None:
        request = {"protocol_version": 1, "operation": "runtime.reconcile", "client": client, "environment": "local"}
        payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            raw = self._run_process(payload, "runtime.reconcile", self.config.reconcile_timeout_seconds)
            self._decode_response(raw, "runtime.reconcile")
        except Exception:
            # Recovery evidence is advisory; the original operation remains indeterminate.
            return

    def invoke(self, request: object) -> dict[str, Any]:
        try:
            validated = validate_request(request)
        except ProtocolError as exc:
            raise BridgeError("INVALID_REQUEST", "invalid operation request") from exc
        payload = (json.dumps(validated, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_REQUEST_BYTES:
            raise BridgeError("INVALID_REQUEST", "operation request exceeds the protocol limit")
        operation = validated["operation"]
        if not self._mutex.acquire(blocking=False):
            raise BridgeError("OPERATION_IN_PROGRESS", "another Odoo operation is in progress")
        try:
            try:
                stdout = self._run_process(payload, operation, self._timeout_for(operation))
            except BridgeError as error:
                if error.code == "OPERATION_TIMED_OUT":
                    self._reconcile(validated["client"])
                raise
            return self._decode_response(stdout, operation)
        finally:
            self._mutex.release()
