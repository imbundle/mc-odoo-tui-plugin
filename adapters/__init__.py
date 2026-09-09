"""Plugin-owned adapters for the read-only Odoo TUI boundary."""

from .odoo_tui_adapter import (
    AdapterError,
    ClientRecord,
    DatabaseRecord,
    InstanceIdentity,
    ModuleRecord,
    OdooTuiAdapter,
    ReleaseRecord,
    RuntimeStatus,
)

__all__ = [
    "AdapterError",
    "ClientRecord",
    "DatabaseRecord",
    "InstanceIdentity",
    "ModuleRecord",
    "OdooTuiAdapter",
    "ReleaseRecord",
    "RuntimeStatus",
]
