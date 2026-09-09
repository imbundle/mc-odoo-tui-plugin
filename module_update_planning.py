"""Pure, translation-aware planning for Odoo module updates.

The planner only consumes backend-confirmed module metadata.  It never talks to
Odoo, starts processes, or writes a plan to disk; callers can serialize the
returned dictionary and ask for approval before applying it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class ModulePlanningError(ValueError):
    """A safe, structured validation error raised while creating an update plan."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class _Module:
    canonical: str
    translated: tuple[str, ...]
    installed: bool
    update_available: bool
    position: int
    source: Mapping[str, Any]


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Mapping):
        return tuple(str(item) for item in value.values() if isinstance(item, str) and item.strip())
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if isinstance(item, str) and item.strip())
    return ()


def _metadata(modules: Iterable[Mapping[str, Any]]) -> list[_Module]:
    if isinstance(modules, Mapping):
        raise ModulePlanningError("INVALID_MODULE_METADATA", "modules must be an iterable of module objects.")
    result: list[_Module] = []
    for position, raw in enumerate(modules):
        if not isinstance(raw, Mapping):
            raise ModulePlanningError("INVALID_MODULE_METADATA", "Each module entry must be an object.")
        canonical = raw.get("canonical_name", raw.get("technical_name", raw.get("name")))
        if not isinstance(canonical, str) or not canonical.strip():
            raise ModulePlanningError("INVALID_MODULE_METADATA", "Each module must have a canonical name.")
        canonical = canonical.strip()
        state = raw.get("state", raw.get("status"))
        installed = raw.get("installed", state == "installed")
        if not isinstance(installed, bool):
            raise ModulePlanningError("INVALID_MODULE_METADATA", f"Invalid installed state for module {canonical!r}.")
        available = raw.get("update_available", raw.get("needs_update", raw.get("to_update", False)))
        if not isinstance(available, bool):
            raise ModulePlanningError("INVALID_MODULE_METADATA", f"Invalid update state for module {canonical!r}.")
        names: list[str] = []
        for key in ("translated_name", "display_name", "translations", "translated_names", "i18n", "name_translations"):
            names.extend(_strings(raw.get(key)))
        # Odoo metadata commonly uses a translated short description as the label.
        names.extend(_strings(raw.get("shortdesc")))
        result.append(_Module(canonical, tuple(dict.fromkeys(names)), installed, available, position, raw))
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in result:
        if item.canonical in seen:
            duplicates.append(item.canonical)
        seen.add(item.canonical)
    if duplicates:
        raise ModulePlanningError(
            "INVALID_MODULE_METADATA",
            "Module metadata contains duplicate canonical names.",
            details={"modules": list(dict.fromkeys(duplicates))},
        )
    return result


def _resolve(modules: list[_Module], identifier: str) -> _Module:
    canonical = [item for item in modules if item.canonical == identifier]
    if len(canonical) == 1:
        return canonical[0]
    matches = [item for item in modules if identifier in item.translated]
    if not matches:
        raise ModulePlanningError("MODULE_NOT_FOUND", f"No module matches {identifier!r}.", details={"identifier": identifier})
    if len(matches) > 1:
        raise ModulePlanningError(
            "AMBIGUOUS_MODULE_TRANSLATION",
            f"Translated module name {identifier!r} matches multiple modules.",
            details={"identifier": identifier, "candidates": [item.canonical for item in matches]},
        )
    return matches[0]


def plan_module_updates(
    modules: Iterable[Mapping[str, Any]],
    *,
    selected: str | Iterable[str] | None = None,
    update_all: bool = False,
) -> dict[str, Any]:
    """Create a deterministic update plan from confirmed module metadata.

    ``selected`` accepts one translated or canonical identifier, or an iterable
    of identifiers.  ``update_all`` plans all installed modules with an update
    available.  The input order is retained; no alphabetical reordering occurs.
    """
    if update_all and selected is not None:
        raise ModulePlanningError("INVALID_SELECTION", "Choose selected modules or update_all, not both.")
    if not update_all and selected is None:
        raise ModulePlanningError("INVALID_SELECTION", "A module selection or update_all=True is required.")
    if isinstance(modules, (str, bytes, bytearray)):
        raise ModulePlanningError("INVALID_MODULE_METADATA", "modules must be an iterable of module objects.")
    catalog = _metadata(modules)
    if not catalog:
        raise ModulePlanningError("EMPTY_UPDATE_SET", "No module metadata is available for update planning.")

    requested_inputs: list[str] = []
    if update_all:
        candidates = [item for item in catalog if item.installed and item.update_available]
        requested = None
        skipped = [item.canonical for item in catalog if not item.installed or not item.update_available]
        unavailable = [item.canonical for item in catalog if not item.installed]
        not_updated = [item.canonical for item in catalog if item.installed and not item.update_available]
    else:
        if isinstance(selected, str):
            identifiers = [selected]
        else:
            try:
                identifiers = list(selected or [])
            except TypeError:
                raise ModulePlanningError(
                    "INVALID_SELECTION",
                    "Selected module identifiers must be a string or iterable of strings.",
                ) from None
        if not identifiers or any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ModulePlanningError("INVALID_SELECTION", "Selected module identifiers must be non-empty strings.")
        resolved: list[_Module] = []
        for identifier in identifiers:
            requested_inputs.append(identifier.strip())
            item = _resolve(catalog, identifier.strip())
            if item not in resolved:
                resolved.append(item)
        unavailable = [item.canonical for item in resolved if not item.installed]
        if unavailable:
            raise ModulePlanningError("MODULE_NOT_INSTALLED", "Selected module is not installed.", details={"modules": unavailable})
        candidates = [item for item in resolved if item.update_available]
        requested = [item.canonical for item in resolved]
        skipped = [item.canonical for item in resolved if not item.update_available]
        not_updated = list(skipped)

    if not candidates:
        raise ModulePlanningError("EMPTY_UPDATE_SET", "No installed modules require an update.", details={"requested": requested or []})

    # Re-sort by catalog position, making selection order irrelevant while
    # preserving the backend's stable module ordering.
    candidates.sort(key=lambda item: item.position)
    planned = [
        {"name": item.canonical, "requested_as": next((name for name in requested_inputs if name == item.canonical or name in item.translated), item.canonical)}
        for item in candidates
    ]
    return {
        "ok": True,
        "kind": "update_all" if update_all else "selected",
        "modules": planned,
        "module_names": [item["name"] for item in planned],
        "skipped": skipped,
        "unavailable": unavailable,
        "not_updated": not_updated,
        "count": len(planned),
        "requires_approval": True,
    }


# Explicit aliases make the contract discoverable from either naming convention.
create_update_plan = plan_module_updates
plan_updates = plan_module_updates
