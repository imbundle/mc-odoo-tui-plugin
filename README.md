# MC Odoo TUI Plugin

External Mission Control plugin prototype.

The current UI milestone validates external plugin discovery and the composable workspace shell. It does not call `odoo-tui` directly; operational data must arrive through the authenticated plugin backend contract.

## Local installation

The repository is canonical under `~/Developer/projects/mc-odoo-tui-plugin`. During local development:

```bash
mkdir -p ~/.hermes/mc-plugins
ln -sfn "$HOME/Developer/projects/mc-odoo-tui-plugin" ~/.hermes/mc-plugins/odoo-tui
cd /path/to/hermes-mission-control
bash scripts/setup-plugins.sh
python3 "$HOME/Developer/projects/mc-odoo-tui-plugin/scripts/verify_plugin_installation.py" \
  --plugin-dir "$HOME/.hermes/mc-plugins/odoo-tui" \
  --source-plugins-dir "$PWD/src/plugins"
```

The host setup script creates `src/plugins/odoo-tui` as a symlink to the installed plugin's `ui/` directory. The verification command is read-only and fails with an actionable error for a missing manifest, incomplete UI, unexpected discovery entries, or an incorrect/dangling symlink.

## API contracts

The bounded, cursor-based log polling semantics are defined in [`docs/log-polling-contract.md`](docs/log-polling-contract.md). The contract is polling-only; SSE and WebSocket transports are out of scope.

## Verification and release

The end-to-end installation, host integration, authentication, test, lifecycle, artifact, versioning, troubleshooting, and release-gate evidence is documented in [`docs/release-and-verification.md`](docs/release-and-verification.md). The document distinguishes verified behavior from unresolved host and test-suite issues; it is not a release approval.
