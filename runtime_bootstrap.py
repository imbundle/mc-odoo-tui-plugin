"""Worker-owned production bootstrap for the fixed read-only adapter."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
if __package__:
    from .adapters.odoo_tui_adapter import OdooTuiAdapter
    from .worker_bridge import OdooTuiWorkerBridge, WorkerConfig, BridgeError
else:
    from adapters.odoo_tui_adapter import OdooTuiAdapter
    from worker_bridge import OdooTuiWorkerBridge, WorkerConfig, BridgeError

CONFIG_PATH = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/config/odoo-tui.yaml")
INTERPRETER_PATH = Path("/home/cyclone/Developer/ODOO/runtime/tools/odoo-tui/.venv/bin/python3")
WORKER_PATH = Path(__file__).with_name("odoo_tui_worker.py")
@dataclass(frozen=True)
class BootstrapState:
    ok: bool; code: str; detail: str
_adapter: OdooTuiAdapter | None = None
_state = BootstrapState(False, "ADAPTER_UNAVAILABLE", "The Odoo TUI adapter is unavailable.")
_lock = Lock()

def bootstrap_adapter() -> BootstrapState:
    global _adapter, _state
    if _adapter is not None: return _state
    with _lock:
        if _adapter is not None: return _state
        try:
            config = WorkerConfig(interpreter=INTERPRETER_PATH, worker=WORKER_PATH, config_path=CONFIG_PATH)
            bridge = OdooTuiWorkerBridge(config)
            # The worker interpreter owns YAML parsing and registry construction;
            # adapter validation also proves both bootstrap collection schemas.
            candidate = OdooTuiAdapter(transport=bridge)
            candidate.list_clients()
            candidate.list_releases()
            _adapter = candidate
            _state = BootstrapState(True, "OK", "The Odoo TUI adapter is ready.")
        except Exception:
            _adapter = None
            _state = BootstrapState(False, "ADAPTER_UNAVAILABLE", "The Odoo TUI adapter is unavailable.")
        return _state

def adapter() -> OdooTuiAdapter | None:
    bootstrap_adapter(); return _adapter

def state() -> BootstrapState:
    bootstrap_adapter(); return _state

def reset_for_tests() -> None:
    global _adapter, _state
    with _lock:
        _adapter = None; _state = BootstrapState(False, "ADAPTER_UNAVAILABLE", "The Odoo TUI adapter is unavailable.")
