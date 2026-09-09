from pathlib import Path
from types import SimpleNamespace
import odoo_tui_worker as worker


def test_malformed_manifest_is_not_silently_omitted(monkeypatch):
    config = SimpleNamespace()
    registry = SimpleNamespace(release_versions=lambda: ["19"], client_names=lambda: ["broken"])
    def bad_selection(*args): raise ValueError("malformed manifest")
    monkeypatch.setattr(worker, "_load", lambda request: ((config, registry, None, None, None, None, bad_selection), None))
    result = worker._read({"operation": "clients.list", "client": "", "environment": "local", "protocol_version": 1})
    assert result["ok"] is False and result["error"]["code"] == "MALFORMED_RESPONSE"


def test_modules_list_reads_states_once(monkeypatch, tmp_path):
    manifest = tmp_path / "base" / "__manifest__.py"
    manifest.parent.mkdir(); manifest.write_text("{}", encoding="utf-8")
    info = SimpleNamespace(name="base", version=None, installable=True, depends=(), manifest=manifest)
    catalog = SimpleNamespace(modules={"base": info})
    calls = []
    postgres = SimpleNamespace(module_states=lambda *args: calls.append(args) or {"base": "installed"})
    selection = SimpleNamespace(config_path=tmp_path / "config.yaml", release=SimpleNamespace(version="19"),
                                client_root=tmp_path, client=SimpleNamespace(name="demo"), database_hint="demo")
    loaded = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), postgres, SimpleNamespace(), selection, None)
    monkeypatch.setattr(worker, "_load", lambda request: (loaded, None))
    loaded = (loaded[0], loaded[1], loaded[2], postgres, SimpleNamespace(from_config=lambda *args, **kwargs: catalog), selection, None)
    monkeypatch.setattr(worker, "_load", lambda request: (loaded, None))
    result = worker._read({"operation": "modules.list", "client": "demo", "environment": "local", "protocol_version": 1})
    assert result["ok"] is True
    assert len(calls) == 1
