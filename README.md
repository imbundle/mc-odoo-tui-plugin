# MC Odoo TUI Plugin

External Mission Control plugin prototype.

The current milestone validates only external plugin discovery and UI rendering. It intentionally has no backend endpoints and does not call `odoo-tui`.

## Local installation

The repository is canonical under `~/Developer/projects/mc-odoo-tui-plugin`. During local development, link it into `~/.hermes/mc-plugins/odoo-tui/`, then run Mission Control's `scripts/setup-plugins.sh` to link the `ui/` directory for Vite.
