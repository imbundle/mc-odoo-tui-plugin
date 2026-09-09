import time

from handlers import apply_module_updates
from module_update_planning import plan_module_updates


class UpdateBoundary:
    def __init__(self, state="running", error=None, update_result=None):
        self.state = state
        self.error = error
        self.update_result = update_result
        self.calls = []

    def status(self, client):
        self.calls.append(("status", client))
        return {"state": self.state}

    def update_modules(self, client, modules):
        self.calls.append(("update_modules", client, tuple(modules)))
        if self.error:
            raise self.error
        if self.update_result is not None:
            return self.update_result
        return {"updated": list(modules)}


def plan(*names, **extra):
    result = {
        "ok": True,
        "kind": "selected",
        "modules": [{"name": name, "requested_as": name} for name in names],
        "module_names": list(names),
        "count": len(names),
        "requires_approval": True,
    }
    result.update(extra)
    return result


def test_approved_update_is_applied_and_read_back():
    boundary = UpdateBoundary()
    result = apply_module_updates("demo", plan("sale", "stock"), boundary, lambda *_: True)
    assert result == {
        "ok": True,
        "code": "updated",
        "client": "demo",
        "modules": ["sale", "stock"],
        "updated": ["sale", "stock"],
        "state": "running",
    }
    assert [call[0] for call in boundary.calls] == ["status", "update_modules", "status"]


def test_rejected_approval_does_not_mutate():
    boundary = UpdateBoundary()
    result = apply_module_updates("demo", plan("sale"), boundary, lambda *_: False)
    assert result["code"] == "approval_denied"
    assert [call[0] for call in boundary.calls] == ["status"]


def test_invalid_and_stale_plans_are_rejected_before_mutation():
    boundary = UpdateBoundary()
    invalid = apply_module_updates("demo", {"ok": True}, boundary, lambda *_: True)
    stale = apply_module_updates("demo", plan("sale", expires_at=time.time() - 1), boundary, lambda *_: True)
    assert invalid["code"] == "invalid_plan"
    assert stale["code"] == "stale_plan"
    assert boundary.calls == []


def test_update_failure_reports_affected_modules_and_readback():
    boundary = UpdateBoundary(error=RuntimeError("secret backend detail"))
    result = apply_module_updates("demo", plan("sale", "stock"), boundary, lambda *_: True)
    assert result["ok"] is False
    assert result["code"] == "update_failure"
    assert result["modules"] == ["sale", "stock"]
    assert result["state"] == "running"
    assert "secret" not in str(result)


def test_timeout_returns_without_waiting_for_slow_update():
    class Slow(UpdateBoundary):
        def update_modules(self, client, modules):
            time.sleep(0.2)

    began = time.monotonic()
    result = apply_module_updates("demo", plan("sale"), Slow(), lambda *_: True, timeout_seconds=0.01)
    assert result["code"] == "timeout"
    assert time.monotonic() - began < 0.15


def test_translated_selection_is_applied_using_canonical_identifier():
    catalog = [
        {"name": "sale", "translations": {"it_IT": "Vendite"}, "installed": True, "update_available": True},
        {"name": "stock", "installed": True, "update_available": True},
    ]
    update_plan = plan_module_updates(catalog, selected="Vendite")
    boundary = UpdateBoundary()

    result = apply_module_updates("demo", update_plan, boundary, lambda *_: True)

    assert result["ok"] is True
    assert result["modules"] == ["sale"]
    assert ("update_modules", "demo", ("sale",)) in boundary.calls


def test_update_all_applies_only_installed_modules_with_updates():
    catalog = [
        {"name": "base", "installed": True, "update_available": True},
        {"name": "sale", "installed": True, "update_available": False},
        {"name": "demo", "installed": False, "update_available": True},
    ]
    update_plan = plan_module_updates(catalog, update_all=True)
    boundary = UpdateBoundary()

    result = apply_module_updates("demo", update_plan, boundary, lambda *_: True)

    assert result["ok"] is True
    assert result["modules"] == ["base"]
    assert ("update_modules", "demo", ("base",)) in boundary.calls


def test_invalid_plan_shape_and_stale_plan_never_call_status_or_update():
    boundary = UpdateBoundary()
    invalid = apply_module_updates(
        "demo",
        {"ok": True, "kind": "selected", "requires_approval": True, "modules": [{"name": "sale"}], "count": 2},
        boundary,
        True,
    )
    stale = apply_module_updates("demo", plan("sale", stale=True), boundary, True)

    assert invalid["code"] == "invalid_plan"
    assert stale["code"] == "stale_plan"
    assert boundary.calls == []


def test_update_readback_failure_is_failure_safe_and_redacted():
    class LostRuntime(UpdateBoundary):
        def update_modules(self, client, modules):
            self.calls.append(("update_modules", client, tuple(modules)))
            self.state = "stopped"
            raise RuntimeError("postgres/pm2 private details")

    boundary = LostRuntime()
    result = apply_module_updates("demo", plan("sale"), boundary, True)

    assert result["ok"] is False
    assert result["code"] == "update_failure"
    assert result["state"] == "stopped"
    assert "private" not in str(result)


def test_partial_update_failure_reports_updated_and_failed_modules():
    boundary = UpdateBoundary(update_result={"updated": ["sale"], "failed": ["stock"]})

    result = apply_module_updates("demo", plan("sale", "stock"), boundary, True)

    assert result == {
        "ok": False,
        "code": "partial_failure",
        "client": "demo",
        "modules": ["sale", "stock"],
        "updated": ["sale"],
        "failed": ["stock"],
        "state": "running",
    }


def test_missing_update_boundary_is_rejected_after_approval_without_mutation():
    class ReadOnly:
        def __init__(self):
            self.calls = []

        def status(self, client):
            self.calls.append(("status", client))
            return {"state": "running"}

    boundary = ReadOnly()
    result = apply_module_updates("demo", plan("sale"), boundary, True)

    assert result["code"] == "operation_not_supported"
    assert [call[0] for call in boundary.calls] == ["status"]
