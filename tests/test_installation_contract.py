import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
VERIFY = ROOT / "scripts" / "verify_plugin_installation.py"


def make_installation(tmp_path: Path) -> tuple[Path, Path, Path]:
    plugin = tmp_path / "plugins" / "odoo-tui"
    plugin.mkdir(parents=True)
    (plugin / "ui").mkdir()
    (plugin / "manifest.json").write_text(
        (ROOT / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (plugin / "ui" / "route.ts").write_text(
        (ROOT / "ui" / "route.ts").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (plugin / "ui" / "manifest.ts").write_text(
        (ROOT / "ui" / "manifest.ts").read_text(encoding="utf-8"), encoding="utf-8"
    )
    host = tmp_path / "mission-control"
    source_plugins = host / "src" / "plugins"
    source_plugins.mkdir(parents=True)
    link = source_plugins / "odoo-tui"
    link.symlink_to(plugin / "ui", target_is_directory=True)
    return plugin, host, link


def run_verifier(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(VERIFY), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_ui_installation_contract_is_runtime_verified(tmp_path):
    plugin, host, link = make_installation(tmp_path)
    result = run_verifier(
        "--plugin-dir",
        str(plugin),
        "--source-plugins-dir",
        str(host / "src" / "plugins"),
    )
    assert result.returncode == 0
    assert "odoo-tui: installation verified" in result.stdout
    assert link.is_symlink()
    assert (link / "route.ts").is_file()
    assert (link / "manifest.ts").is_file()


def test_installation_verifier_accepts_exact_ui_symlink(tmp_path):
    plugin, host, link = make_installation(tmp_path)

    result = run_verifier(
        "--plugin-dir",
        str(plugin),
        "--source-plugins-dir",
        str(host / "src" / "plugins"),
    )

    assert result.returncode == 0
    assert "odoo-tui: installation verified" in result.stdout
    assert link.resolve() == (plugin / "ui").resolve()


def test_installation_verifier_reports_missing_plugin(tmp_path):
    result = run_verifier(
        "--plugin-dir",
        str(tmp_path / "missing"),
        "--source-plugins-dir",
        str(tmp_path / "host" / "src" / "plugins"),
    )

    assert result.returncode != 0
    assert "plugin directory does not exist" in result.stderr


def test_installation_verifier_reports_invalid_symlink(tmp_path):
    plugin, host, link = make_installation(tmp_path)
    wrong_ui = tmp_path / "wrong-ui"
    wrong_ui.mkdir()
    link.unlink()
    link.symlink_to(wrong_ui, target_is_directory=True)

    result = run_verifier(
        "--plugin-dir",
        str(plugin),
        "--source-plugins-dir",
        str(host / "src" / "plugins"),
    )

    assert result.returncode != 0
    assert "must point to" in result.stderr
