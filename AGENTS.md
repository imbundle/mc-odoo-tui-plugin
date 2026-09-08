# AGENTS.md — MC Odoo TUI Plugin

## Project Context

- Project ID: `mc-odoo-tui-plugin`
- Kind: `proprietary`
- Scope: `repository | combined` — to confirm
- Primary repository: `https://github.com/imbundle/mc-odoo-tui-plugin`
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
- The plugin must provide a backend service or service adapter as the only boundary between Mission Control and odoo-tui.
- Mission Control and odoo-tui are external read-only dependencies for this project: do not modify either repository, runtime contract, CLI, package API, or service topology.
- All integration logic, including configuration reading, password retrieval, data mapping, and command orchestration, belongs in the plugin backend.
- The frontend must never call odoo-tui directly, invoke its CLI, spawn arbitrary processes, or construct operational commands.
- The backend service must expose typed, authenticated, allowlisted operations and structured results/errors.
- The service must preserve odoo-tui as the operational authority for validation, safety checks, lifecycle semantics, and evidence generation.
- The adapter must define bounded timeouts, failure handling, redaction, and request/response logging without secrets.
- The administrator password is a deliberate sensitive capability: normal responses expose only presence/state; a separate authenticated request may return the clear value when explicitly required. The value must never be logged, persisted, cached, or included in generic errors/evidence.
- The transport between the plugin service and odoo-tui remains an explicit design decision; do not assume an HTTP API exists.

## Development Rules

- Inspect the current Mission Control plugin contract before changing the plugin boundary.
- Keep plugin-specific code separate from host core code.
- Preserve unrelated changes and never commit secrets, tokens, `.env` files, or local runtime state.
- Separate read-only status/data operations from mutating lifecycle or update operations.
- Require confirmed backend state before enabling mutating actions.
- Verify the result of every mutating action by reading back the target state.
- Do not start, stop, restart, update, create, import, or delete real Odoo resources during frontend development tests.

## Git Commit Rules

- Use the format `[TAG][AREA] lowercase concise description`.
- Use uppercase tags: `ADD`, `IMP`, `FIX`, `REF`, `REM`, `MOV`, `DOC`, `TEST`, `VER`, or `CHG`.
- Use a controlled technical area: `front`, `back`, `api`, `git`, `test`, `docs`, `config`, `ci`, or `infra`.
- Keep the subject concise, normally within 72 characters.
- Start the description with a lowercase imperative verb: `add`, `improve`, `fix`, `refactor`, `remove`, `move`, `document`, `test`, `verify`, or `change`.
- Keep one coherent change per commit; do not mix unrelated areas.
- Use the body when the reason or design trade-off is not clear from the subject.
- Do not include credentials, private URLs, ticket data, `.env` files, local runtime state, or generated private data.

Examples:

```text
[ADD][front] add Odoo instance dashboard
[FIX][back] reject unauthenticated lifecycle actions
[REF][api] extract odoo-tui adapter
[DOC][git] document commit conventions
[TEST][front] cover plugin route rendering
[CHG][config] update Vite plugin configuration
```

## Versioning Rules

- Use Semantic Versioning for the plugin: `MAJOR.MINOR.PATCH`.
- Start the plugin at version `0.1.0` while the public contract is still evolving.
- Increment `PATCH` for backward-compatible bug fixes.
- Increment `MINOR` for backward-compatible new functionality.
- Increment `MAJOR` for breaking changes to the plugin contract, integration boundary, configuration, or supported behavior.
- Use pre-release versions when needed: `0.1.0-alpha.1`, `0.1.0-beta.1`, or `0.1.0-rc.1`.
- Keep the plugin version independent from the Mission Control version and the odoo-tui adapter contract version.
- Keep the manifest version, package version, and GitHub release tag aligned.
- Use annotated release tags in the form `vMAJOR.MINOR.PATCH`.
- Update the changelog and run the required checks before creating a release.
- Use `[VER][git] release plugin X.Y.Z` for the release commit.

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
