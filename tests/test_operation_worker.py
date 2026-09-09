import pytest
from dataclasses import dataclass
from types import SimpleNamespace

from operation_worker import ProtocolError, _UpdateLogVerifier, execute, validate_request


def request(operation, **extra):
    return {
        "protocol_version": 1,
        "operation": operation,
        "client": "acme",
        "environment": "local",
        **extra,
    }


@dataclass(frozen=True)
class _LogReport:
    new_errors: tuple[str, ...]


class _LogVerifier:
    def analyze(self, checkpoint):
        return _LogReport((
            "2026-09-09 WARNING Error-prone use of @class in view foo",
            "2026-09-09 ERROR real module loading failure",
        ))


def test_update_log_verifier_does_not_block_known_warning_but_keeps_real_error():
    report = _UpdateLogVerifier(_LogVerifier()).analyze(None)

    assert report.new_errors == ("2026-09-09 ERROR real module loading failure",)


def test_start_delegates_client_controller_and_requires_online_readback():
    calls = []

    class Controller:
        def start(self, selection, mode, *, allow_concurrent):
            calls.append(("start", selection, mode, allow_concurrent))
            return SimpleNamespace(verdict="PASS")

    class PM2:
        def assert_contract(self, spec):
            calls.append(("assert_contract", spec))
            return SimpleNamespace(status="stopped")

        def status(self, name):
            calls.append(("status", name))
            return SimpleNamespace(status="online")

    context = SimpleNamespace(
        config=SimpleNamespace(single_active_instance=False),
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local"),
        client_spec=SimpleNamespace(name="odoo-19-acme-local"),
        controller=Controller(),
        pm2=PM2(),
    )
    result = execute(request("lifecycle.start", confirmation="START acme"), context_factory=lambda _: context)
    assert result == {
        "protocol_version": 1,
        "operation": "lifecycle.start",
        "ok": True,
        "data": {"state": "online", "control_mode": "client"},
    }
    assert calls[0][0] == "assert_contract"
    assert calls[1] == ("start", context.selection, "Client", True)


def test_non_pass_controller_result_is_operation_failure():
    class Controller:
        def start(self, selection, mode, *, allow_concurrent):
            return SimpleNamespace(verdict="FAIL")

    class PM2:
        def assert_contract(self, spec):
            return SimpleNamespace(status="stopped")

    context = SimpleNamespace(
        config=SimpleNamespace(single_active_instance=False),
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local"),
        client_spec=SimpleNamespace(name="odoo-19-acme-local"),
        controller=Controller(),
        pm2=PM2(),
    )
    result = execute(request("lifecycle.start", confirmation="START acme"), context_factory=lambda _: context)
    assert result["ok"] is False
    assert result["error"]["code"] == "OPERATION_FAILED"


def test_selected_update_uses_fresh_controller_plan_and_exact_confirmation():
    calls = []

    class Controller:
        def plan_update(self, selection, database, module_input, catalog, *, database_mode, update_all):
            calls.append((database, module_input, catalog, database_mode, update_all))
            return SimpleNamespace(modules=("base", "sale"), database=database, confirmation="UPDATE " + database)

        def execute_update(self, plan, confirmation):
            calls.append(("execute", plan, confirmation))
            return SimpleNamespace(verdict="PASS")

    class PM2:
        def assert_contract(self, spec):
            return SimpleNamespace(status="online")

        def status(self, name):
            return SimpleNamespace(status="online")

    context = SimpleNamespace(
        config=SimpleNamespace(single_active_instance=False),
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local", database_hint="19_acme"),
        client_spec=SimpleNamespace(name="odoo-19-acme-local"),
        catalog=object(),
        controller=Controller(),
        pm2=PM2(),
    )
    planned = execute(request("updates.plan", modules=["base", "sale"]), context_factory=lambda _: context)
    assert planned == {
        "protocol_version": 1,
        "operation": "updates.plan",
        "ok": True,
        "data": {"client": "acme", "kind": "selected", "database": "19_acme", "modules": ["base", "sale"], "confirmation": "UPDATE 19_acme"},
    }
    applied = execute(request("updates.apply", modules=["base", "sale"], confirmation="UPDATE 19_acme"), context_factory=lambda _: context)
    assert applied["ok"] is True
    assert applied["data"] == {"client": "acme", "kind": "selected", "modules": ["base", "sale"], "state": "online"}
    assert calls[0] == ("19_acme", "base,sale", context.catalog, "Client", False)
    assert calls[1] == ("19_acme", "base,sale", context.catalog, "Client", False)
    assert calls[2][0] == "execute"



def test_restart_with_selected_modules_updates_then_returns_online():
    calls = []

    class Controller:
        def plan_update(self, selection, database, module_input, catalog, *, database_mode, update_all):
            calls.append(("plan", database, module_input, database_mode, update_all))
            return SimpleNamespace(modules=("base",), database=database, confirmation="UPDATE " + database)

        def execute_update(self, plan, confirmation):
            calls.append(("execute", confirmation))
            return SimpleNamespace(verdict="PASS")

    class PM2:
        def assert_contract(self, spec):
            calls.append(("assert_contract", spec))
            return SimpleNamespace(status="online")

        def status(self, name):
            calls.append(("status", name))
            return SimpleNamespace(status="online")

    context = SimpleNamespace(
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local", database_hint="19_acme"),
        client_spec=SimpleNamespace(name="odoo-19-acme-local"),
        catalog=object(),
        controller=Controller(),
        pm2=PM2(),
    )
    result = execute(request("lifecycle.restart", modules=["base"], confirmation="RESTART acme"), context_factory=lambda _: context)

    assert result == {
        "protocol_version": 1,
        "operation": "lifecycle.restart",
        "ok": True,
        "data": {"client": "acme", "state": "online", "control_mode": "client", "modules": ["base"]},
    }
    assert calls[1] == ("plan", "19_acme", "base", "Client", False)
    assert calls[2] == ("execute", "UPDATE 19_acme")


def test_reconcile_reads_pm2_and_database_only():
    calls = []

    class PM2:
        def status(self, name):
            calls.append(("status", name))
            return SimpleNamespace(status="online")

    class Postgres:
        def database_exists(self, name):
            calls.append(("database_exists", name))
            return True

    context = SimpleNamespace(
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local", database_hint="19_acme"),
        pm2=PM2(),
        postgres=Postgres(),
    )
    result = execute(request("runtime.reconcile"), context_factory=lambda _: context)
    assert result == {
        "protocol_version": 1,
        "operation": "runtime.reconcile",
        "ok": True,
        "data": {"state": "online", "control_mode": "unknown", "database_exists": True},
    }
    assert calls == [("status", "odoo-19-acme-local"), ("database_exists", "19_acme")]


@pytest.mark.parametrize("operation", ["lifecycle.start", "lifecycle.stop", "lifecycle.restart"])
def test_lifecycle_confirmation_is_bound_to_operation(operation):
    wrong_confirmation = {
        "lifecycle.start": "STOP acme",
        "lifecycle.stop": "START acme",
        "lifecycle.restart": "START acme",
    }[operation]
    with pytest.raises(ProtocolError, match="confirmation"):
        validate_request(request(operation, confirmation=wrong_confirmation))


def test_selected_update_requires_unique_canonical_modules():
    validated = validate_request(request("updates.plan", modules=["base", "sale_management"]))
    assert validated["modules"] == ["base", "sale_management"]

    with pytest.raises(ProtocolError):
        validate_request(request("updates.plan", modules=["base", "base"]))


def test_update_all_and_reconcile_have_closed_shapes():
    assert validate_request(request("updates.plan", update_all=True))["update_all"] is True
    assert validate_request(request("runtime.reconcile")) == request("runtime.reconcile")

    with pytest.raises(ProtocolError):
        validate_request(request("runtime.reconcile", confirmation="unexpected"))




def test_unknown_fields_and_invalid_client_are_rejected():
    with pytest.raises(ProtocolError):
        validate_request(request("lifecycle.stop", confirmation="STOP acme", argv=[]))
    with pytest.raises(ProtocolError):
        validate_request(request("lifecycle.stop", confirmation="STOP ../acme"))


def test_database_manager_start_uses_selected_mode():
    calls = []

    class Controller:
        def start(self, selection, mode, *, allow_concurrent):
            calls.append(mode)
            return SimpleNamespace(verdict="PASS")

    class PM2:
        def assert_contract(self, spec):
            return SimpleNamespace(status="stopped")

        def status(self, name):
            return SimpleNamespace(status="online")

    context = SimpleNamespace(
        config=SimpleNamespace(single_active_instance=False),
        selection=SimpleNamespace(pm2_process="odoo-19-acme-local"),
        client_spec=SimpleNamespace(name="odoo-19-acme-local"),
        manager_spec=SimpleNamespace(name="odoo-19-acme-local"),
        controller=Controller(),
        pm2=PM2(),
    )
    result = execute(request("lifecycle.start", confirmation="START acme", mode="database_manager"), context_factory=lambda _: context)

    assert result["ok"] is True
    assert result["data"] == {"state": "online", "control_mode": "database_manager"}
    assert calls == ["Database Manager"]


def test_lifecycle_mode_is_closed_and_updates_require_client_mode():
    with pytest.raises(ProtocolError, match="mode"):
        validate_request(request("lifecycle.start", confirmation="START acme", mode="root"))
    with pytest.raises(ProtocolError, match="mode"):
        validate_request(request("lifecycle.stop", confirmation="STOP acme", mode="client"))
    rejected = execute(request("lifecycle.restart", confirmation="RESTART acme", mode="database_manager", modules=["base"]), context_factory=lambda _: object())
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "PRECONDITION_FAILED"
