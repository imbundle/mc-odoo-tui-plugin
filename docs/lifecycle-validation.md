# Plugin lifecycle validation

Validated 2026-09-08 in an isolated temporary workspace. The Mission Control checkout was copied without `.git`, `.env`, `dist/`, or `node_modules/`; the existing host `node_modules/` was mounted read-only through a symlink. `HOME` pointed to the temporary workspace, so the real `~/.hermes/mc-plugins` and the real Mission Control checkout were not changed.

## Versions

- Plugin manifest before install: `0.1.0`
- Plugin manifest during update simulation: `0.1.1`
- Mission Control package: `0.1.0`
- Vite: `6.4.3` (from the installed lockfile/dependency tree)

## Commands exercised

For each lifecycle phase, the host was started with:

    pnpm exec vite --host 127.0.0.1 --port 5174

Readiness was verified by fetching `http://127.0.0.1:5174/` and checking for the HTML document. The process was terminated after the check.

Baseline, before plugin installation:

    pnpm exec vite --host 127.0.0.1 --port 5174

Install and link:

    bash scripts/setup-plugins.sh
    pnpm build
    pnpm exec vite --host 127.0.0.1 --port 5174

Repeatability:

    bash scripts/setup-plugins.sh
    pnpm build

Update simulation (the plugin version was changed from `0.1.0` to `0.1.1` in the isolated installed copy):

    bash scripts/setup-plugins.sh
    pnpm build
    pnpm exec vite --host 127.0.0.1 --port 5174

Removal:

    rm -rf "$HOME/.hermes/mc-plugins/odoo-tui"
    bash scripts/setup-plugins.sh
    pnpm build
    pnpm exec vite --host 127.0.0.1 --port 5174

## Observed results

- Host startup before installation: PASS; Vite served the index document.
- Clean install: PASS; setup reported `Linking odoo-tui` and created `src/plugins/odoo-tui` pointing to the installed copy's `ui/` directory.
- Build after install: PASS; `1275 modules transformed`, build completed successfully.
- Host startup after install: PASS.
- Repeated setup: PASS; setup reported `odoo-tui: already linked` and `0 plugin(s) linked`.
- Build after repeated setup: PASS; `1275 modules transformed`.
- Update simulation: PASS; setup remained repeatable, manifest version was `0.1.1`, and the build completed successfully with `1275 modules transformed`.
- Host startup after update: PASS.
- Build after removal: PASS; plugin modules disappeared from the build (`1271 modules transformed`).
- Host startup after removal: PASS; the host remained usable without the plugin.

## Removal finding

The documented removal sequence does not fully clean the host UI link:

- After deleting the installed plugin directory, `bash scripts/setup-plugins.sh` reported `0 plugin(s) linked`.
- `src/plugins/odoo-tui` remained as a dangling symlink to the deleted `ui/` directory.
- The reason is in Mission Control's `scripts/setup-plugins.sh`: the stale-link loop uses the glob `"$SRC_PLUGINS_DIR"/*/`, which does not enumerate a dangling symlink when the pattern has a trailing slash.
- The subsequent Vite build and host startup still passed because Vite ignores the broken link, but the acceptance requirement of no stale files is not met.

Temporary manual cleanup, if uninstalling against the current host script:

    rm -f /path/to/hermes-mission-control/src/plugins/odoo-tui

This host-side workaround was not applied to the real checkout. Fixing the stale-link glob belongs in Mission Control, which is an external read-only dependency for this plugin repository.

## Repository hygiene

The plugin repository's ignore rules exclude `__pycache__/`, bytecode, `node_modules/`, `dist/`, and `.env` files. The lifecycle test used only temporary copies and did not write secrets, runtime state, or generated artifacts into this repository. Existing working-tree changes were preserved and not modified by this validation.

## Final status

Install, repeatability, update, build, and host startup are verified. Uninstall is functionally absent from the running host but fails the strict stale-file criterion because of the Mission Control setup-script defect described above.
