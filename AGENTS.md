# AGENTS.md — MC Odoo TUI Plugin

## Project Context

- Project ID: `mc-odoo-tui-plugin`
- Kind: `proprietary`
- Scope: `repository | combined` — to confirm
- Primary repository: `TBD — Davide's plugin repository`
- Workspace: `/home/cyclone/Developer/projects/mc-odoo-tui-plugin`
- Host integration: Mission Control plugin system
- Source of truth: this repository, the Mission Control plugin contract, and the approved project plan

## Skill Entry Point

- Start non-trivial project work with `project-skill-router`.
- Use the approved project plan as the source of implementation scope.
- Use `project-execution` only after the plan is `APPROVED`.
- Do not treat this file as authorization to modify Mission Control, odoo-tui, Odoo instances, or runtime services.

## Architecture Rules

- The plugin owns its feature-specific route, state, hooks, API adapter, and components.
- Self-contained does not mean that the plugin must reimplement Mission Control's design system.
- Reuse Mission Control shared UI components, layout primitives, Lucide icons, Tailwind utilities, CSS custom properties, global typography, colors, spacing, and global styles whenever the integration boundary exposes them.
- Add plugin-local CSS only for behavior or visual details specific to the plugin.
- Do not modify global styles to solve a plugin-local problem unless the change is intentionally part of the shared design system.
- Preserve Mission Control's visual language and interaction conventions.
- If the plugin is developed in a separate repository, consume shared components and styles through an explicit supported integration boundary; do not silently duplicate them.
- Keep the frontend graphical. Do not use the terminal TUI as the primary frontend interface.
- Keep Odoo lifecycle and safety semantics in the odoo-tui/backend adapter layer; do not reimplement them inconsistently in React.

## Development Rules

- Inspect the current Mission Control plugin contract before changing the plugin boundary.
- Keep plugin-specific code separate from host core code.
- Preserve unrelated changes and never commit secrets, tokens, `.env` files, or local runtime state.
- Separate read-only status/data operations from mutating lifecycle or update operations.
- Require confirmed backend state before enabling mutating actions.
- Verify the result of every mutating action by reading back the target state.
- Do not start, stop, restart, update, create, import, or delete real Odoo resources during frontend development tests.

## Verification

- Run focused plugin tests first.
- Verify manifest, route, sidebar registration, shared component usage, and error/loading states.
- Run the host integration build and test commands documented by Mission Control.
- Verify desktop and mobile layouts.
- Report exactly what was verified; do not claim external-plugin loading works without an implemented and tested integration boundary.

## Protected Areas

- Do not modify Hermes Core.
- Do not modify odoo-tui runtime semantics without an explicit approved scope.
- Do not alter Odoo databases, filestores, secrets, configuration, or process supervision as part of frontend work.
- Do not commit local runtime state, generated private data, or credentials.
- Do not begin implementation while the primary repository or integration model remains `TBD`.
