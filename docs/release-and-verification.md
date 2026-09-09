# Release and verification workflow

This document is the contributor and release checklist for the external Mission Control plugin. It records the validation evidence gathered for issue 9 and separates verified behavior from defects that remain outside this repository's scope.

## Scope and prerequisites

The plugin is an external repository. Mission Control and `odoo-tui` are read-only dependencies for this project; do not modify either checkout as part of plugin verification.

You need:

- a clean checkout of this repository;
- a Mission Control checkout with its documented dependencies installed;
- Python 3.11 or a compatible supported Python for plugin checks;
- Node.js and the package-manager version declared by the Mission Control checkout;
- a temporary `HOME` for integration tests;
- a Mission Control local-telemetry bearer token supplied through the test environment, never committed or printed;
- an installed `odoo-tui` environment only when exercising the worker bridge against a real runtime.

Use temporary copies and loopback ports for integration checks. Do not create, import, delete, or mutate Odoo databases or real runtime resources.

## Clean checkout and local installation

From a clean checkout of the plugin:

```bash
git clone https://github.com/imbundle/mc-odoo-tui-plugin.git
cd mc-odoo-tui-plugin
git status --short
python3 -m json.tool manifest.json >/dev/null
python3 -m compileall -q endpoints.py handlers.py adapters worker_bridge.py odoo_tui_worker.py
```

`git status --short` should be empty. The JSON and Python checks should exit with status 0.

For local development, install the repository under the host's external-plugin directory and link its UI:

```bash
mkdir -p "$HOME/.hermes/mc-plugins"
ln -sfn "$PWD" "$HOME/.hermes/mc-plugins/odoo-tui"
cd /path/to/hermes-mission-control
bash scripts/setup-plugins.sh
python3 /path/to/mc-odoo-tui-plugin/scripts/verify_plugin_installation.py \
  --plugin-dir "$HOME/.hermes/mc-plugins/odoo-tui" \
  --source-plugins-dir "$PWD/src/plugins"
```

Expected result: the host reports `Linking odoo-tui` on a fresh install, and the verifier reports `odoo-tui: installation verified`. A repeated setup is expected to report that the plugin is already linked and should not create duplicate entries. The backend reads the repository under `~/.hermes/mc-plugins/odoo-tui`; the frontend discovers the linked `ui/` directory under `src/plugins/odoo-tui`.

## Host behavior with and without the plugin

Run these checks in a disposable Mission Control copy with a temporary `HOME`. Start telemetry and the host frontend according to the host README. The frontend smoke command used by lifecycle validation was:

```bash
pnpm exec vite --host 127.0.0.1 --port 5174
```

Fetch `/` and confirm that an HTML document is served before installation, after installation, after an update, and after removal. The host must remain usable without the plugin.

The validated host checks produced these results:

- without the plugin: unauthenticated `/api/local/system` returned HTTP 401; an incorrect bearer returned HTTP 401; a valid bearer returned HTTP 200 with the local health payload; unknown plugin routes returned HTTP 404;
- with the plugin installed: the same core authentication and health behavior remained intact, and the plugin UI build passed;
- focused UI checks passed: `pnpm run test:mobile-route-layout` and `pnpm run test:vite-config`;
- `pnpm run build` passed both without the plugin and with its UI linked.

The first integration report exercised concrete dynamic instance paths before the later static-path refactor and observed HTTP 404 because the host loader performs exact path matching. It also found that the host HTTP dispatch did not forward its authenticated context to plugin handlers. These are host integration defects, not evidence that the plugin should modify Mission Control. Re-run live endpoint checks against the current static paths before calling the integration gate green.

## Endpoint and authentication checks

The manifest declares exactly six read-only paths under `/api/local` and requires authentication for every endpoint. Instance operations select the resource with a URL-encoded `client` query parameter:

```text
GET  /api/local/odoo-tui/clients
GET  /api/local/odoo-tui/releases
GET  /api/local/odoo-tui/instance/identity?client=<client>
GET  /api/local/odoo-tui/instance/status?client=<client>
GET  /api/local/odoo-tui/instance/modules?client=<client>
GET  /api/local/odoo-tui/instance/databases?client=<client>
```

For each harmless read endpoint, check missing, wrong, and valid bearer credentials. Expected missing or wrong credentials are HTTP 401 and must not invoke the adapter. Check missing or unknown `client` values and malformed query/body data; expected results are structured HTTP 400 errors without filesystem paths, secrets, or tracebacks. Check the mode write endpoint with an authenticated but unauthorized context; expected result is HTTP 403. Never call lifecycle or update operations against a real instance; use only mocked or unregistered operation boundaries for those contract tests.

The direct handler evidence is fail-closed: missing authentication returned `401 UNAUTHENTICATED`, authenticated-only writes returned `403 FORBIDDEN`, and authenticated requests reached resource validation. Live HTTP endpoint integration was not green in the earlier host snapshot because of the exact-path and auth-context defects above; this remains an unresolved release gate until verified against a host revision that supports the current contract.

The log API is bounded cursor polling, not SSE or WebSocket. Its detailed shape and validation rules are in [`log-polling-contract.md`](log-polling-contract.md). The worker bridge is read-only and its JSON boundary is in [`worker-bridge-protocol.md`](worker-bridge-protocol.md).

## Automated tests and build

Plugin checks:

```bash
pytest -q
pytest -q tests/test_mode_endpoints.py tests/test_log_polling_impl.py tests/test_module_update_planning.py
```

Fresh verification of the current checkout passes the full plugin suite (`133 passed`) and the focused integration/contract selection (`35 passed`). Earlier concurrent snapshots recorded collection and fixture failures while sibling changes were still landing; those historical failures are retained here only as context and are not current results. A `no tests collected` result is not a pass.

Host checks, from the Mission Control checkout:

```bash
pnpm build
pnpm test
pnpm run test:mobile-route-layout
pnpm run test:vite-config
pnpm run test:system-health-ui
```

The recorded host build and focused checks passed. The full host suite ran 147 tests and had one failure in `test_synthesis_activity_proxy`: the test expected `127.0.0.1:8643`, while the environment returned `127.0.0.1:9010`. This is an unrelated host/environment discrepancy and must not be hidden or fixed in the plugin.

## Install, update, and removal

Use a disposable host copy and temporary `HOME`:

1. Start the host before installation and verify `/` serves HTML.
2. Install the plugin, run `bash scripts/setup-plugins.sh`, run the read-only verifier, build, and start the host.
3. Run setup a second time; it should be idempotent.
4. Copy the installed plugin and change only its manifest version from `0.1.0` to `0.1.1`; run setup, build, and startup again.
5. Remove the installed plugin directory, run setup, build, and startup, then verify that no plugin UI remains.

These lifecycle phases were verified in an isolated workspace: baseline startup, clean install, repeatable setup, `0.1.0` to `0.1.1` update simulation, builds, and startup all passed. Removal was functionally successful and the host still built and started, but the current host setup script left a dangling `src/plugins/odoo-tui` symlink after the installed directory was deleted. Its stale-link glob `src/plugins/*/` does not match dangling symlinks. For validation only, remove the stale link explicitly:

```bash
rm -f /path/to/hermes-mission-control/src/plugins/odoo-tui
pnpm build
```

This workaround belongs to the disposable host checkout. Do not change or commit Mission Control from this repository. Strict removal remains unresolved until the host script handles dangling symlinks.

## Versioning and release steps

The canonical plugin version is currently `0.1.0`, present in both `manifest.json` and `ui/manifest.ts`. The Mission Control package version is independent and must not be used as the plugin release version. The repository currently has no plugin `package.json`, Python package metadata, lockfile, changelog, GitHub Actions release workflow, or artifact configuration. Therefore no automated release artifact or release workflow is verified yet.

When a release process is introduced, it must:

1. update the plugin version consistently in `manifest.json` and `ui/manifest.ts`;
2. use semantic versions, with pre-releases such as `0.1.0-alpha.1` when needed;
3. create an annotated tag named `vMAJOR.MINOR.PATCH`;
4. package only the installable plugin contents (`manifest.json`, backend Python modules, adapters, scripts/docs required by the chosen distribution, and `ui/`), excluding `.git`, `.env*`, `__pycache__`, bytecode, `node_modules`, `dist`, caches, logs, credentials, and machine-specific paths;
5. make the artifact name and release tag derive from the same canonical version;
6. run all focused checks, the full plugin suite, host integration checks, and artifact inspection before publishing;
7. use a release commit subject in the form `[VER][git] release plugin X.Y.Z`.

Until that metadata and workflow exist, the correct status is `not release-ready`, not a fabricated release pass.

## Artifact hygiene

Inspect the exact candidate archive before publication:

```bash
git archive --format=tar --prefix=odoo-tui/ HEAD > /tmp/odoo-tui.tar
mkdir -p /tmp/odoo-tui-artifact
tar -xf /tmp/odoo-tui.tar -C /tmp/odoo-tui-artifact
find /tmp/odoo-tui-artifact -type f -print
```

The archive must contain only intended tracked plugin files. The earlier clean-HEAD inspection found no tracked build output, `node_modules`, bytecode, `.env`, runtime state, or generated private artifacts. A dirty working tree is not evidence of a dirty release: inspect `git archive HEAD`, and do not stage concurrent or unrelated changes. Delete temporary archives after inspection.

## Troubleshooting and release blockers

- `installation verifier` rejects the plugin: confirm `manifest.json`, `ui/manifest.ts`, and `ui/route.ts` exist, and that `src/plugins/odoo-tui` points to the installed `ui/` directory rather than a dangling link.
- Host returns 404 for a concrete instance URL: confirm the manifest uses the current exact static path plus `?client=...`; the host does not interpret `{client}` path templates.
- Authenticated requests fail inside plugin handlers: verify the host dispatch passes its authenticated context to the handler. Do not weaken plugin-side authentication checks.
- Full plugin pytest collection or tests fail: fix the test/implementation mismatch or fixture setup, then rerun the complete suite; do not report focused tests as a substitute.
- Host synthesis proxy test fails on port `8643` versus `9010`: treat it as the recorded host/environment discrepancy and investigate in the host project separately.
- Removal leaves `src/plugins/odoo-tui`: manually remove the dangling symlink in the disposable host checkout, and track the host setup-script defect separately.
- Release artifact contains local paths or secrets: discard it, inspect ignore rules and archive contents, and rebuild from a clean checkout. Never redact after the fact and publish.

## Release decision

The plugin has verified installation, update, build, startup, focused contract checks, full plugin tests, backend bridge tests, and artifact hygiene evidence. It is not release-ready while live endpoint integration/auth-context behavior, strict uninstall cleanup, and release metadata/workflow remain unresolved. The unresolved items are explicitly documented here so they cannot be mistaken for successful validation.
