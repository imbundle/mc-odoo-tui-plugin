import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/odoo-tui',
});
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  MutationObserver: dom.window.MutationObserver,
});
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });

const { createElement } = await import('react');
const { render, screen, waitFor } = await import('@testing-library/react');
const userEvent = (await import('@testing-library/user-event')).default;
const { default: OdooTuiRoute } = await import('./OdooTuiRoute');

const calls: string[] = [];
const clients = [{ name: 'acme', release: '19', environment: 'local', local_url: null }, { name: 'beta', release: '19', environment: 'local', local_url: null }];
const snapshotClient = (client: string) => clients.find((item) => item.name === client)!;
const snapshotIdentity = (client: string) => JSON.stringify([client, '19', 'local', null]);
const snapshotRegistryIdentity = JSON.stringify(clients.map((client) => [client.name, snapshotIdentity(client.name)]));
const snapshot = (client: string, state: 'online' | 'stopped' = 'online') => ({
  registry_identity: snapshotIdentity(client),
  identity: { client, release: '19', environment: 'local', database: `19_${client}`, http_port: client === 'beta' ? 8070 : 8069, longpolling_port: client === 'beta' ? 8073 : 8072 },
  status: { state, pid: state === 'online' ? 123 : null, process_name: `odoo-19-${client}-local`, pm2_id: client === 'beta' ? 2 : 1, startup_mode: null },
  control: { control_mode: 'client' as const, lifecycle_eligible: true, reason: null },
  modules: [{ name: 'base', version: '1.0', installed: true, installable: true, update_available: false, dependencies: [] }, { name: 'demo', version: '1.0', installed: false, installable: true, update_available: false, dependencies: [] }],
  databases: [{ name: `19_${client}`, exists: true }],
});
const getSnapshot = async (client?: string) => client ? { protocol_version: 1 as const, process_instance_id: '0123456789abcdef0123456789abcdef', registry_epoch: 0, registry_identity: snapshotRegistryIdentity, releases: [{ version: '19' }], releases_error: null, client, snapshot: snapshot(client), errors: {} } : { protocol_version: 1 as const, process_instance_id: '0123456789abcdef0123456789abcdef', registry_epoch: 0, registry_identity: snapshotRegistryIdentity, clients: clients.map((item) => ({ ...item, registry_identity: snapshotIdentity(item.name) })), releases: [{ version: '19' }], releases_error: null, errors: {} };
const api = {
  getSnapshot,
  listClients: async () => ({ clients }),
  listReleases: async () => ({ releases: [{ version: '19' }] }),
  getIdentity: async () => ({ instance: { client: 'acme', release: '19', environment: 'local', config_identity: null, database: '19_acme', http_port: 8069, longpolling_port: 8072 } }),
  getStatus: async (client = 'acme') => ({ status: { state: client === 'beta' ? 'stopped' as const : 'online' as const, pid: client === 'beta' ? null : 123, process_name: `odoo-19-${client}-local`, pm2_id: client === 'beta' ? 2 : 1, startup_mode: null } }),
  getControl: async () => ({ control: { control_mode: 'client', lifecycle_eligible: true, reason: null } }),
  getModules: async () => ({ client: 'acme', modules: [{ name: 'base', version: '1.0', installed: true, installable: true, update_available: false, dependencies: [] }, { name: 'demo', version: '1.0', installed: false, installable: true, update_available: false, dependencies: [] }] }),
  getDatabases: async () => ({ client: 'acme', databases: [{ name: '19_acme', exists: true }] }),
  getLogs: async () => ({ entries: [{ timestamp: '2026-09-09T10:00:00Z', pid: 123, level: 'INFO', database: '19_acme', logger: 'odoo.modules.loading', message: 'Odoo worker ready' }, { timestamp: '2026-09-09T10:01:02.123Z', pid: 123, level: 'WARNING', database: '19_acme', logger: 'odoo.sql_db', message: 'Connection pool is busy' }, { timestamp: '2026-09-09T10:02:03.456Z', pid: 123, level: 'ERROR', database: '19_acme', logger: 'odoo.http', message: 'Request failed' }], next_cursor: 'cursor', has_more: false, cursor_reset: false }),
  start: async () => { calls.push('start'); return { client: 'acme', operation: 'start', state: 'online' }; },
  stop: async () => { calls.push('stop'); return { client: 'acme', operation: 'stop', state: 'stopped' }; },
  restart: async (_client: string, _confirmation: string, selector?: { modules: string[] } | { update_all: true }) => { calls.push(`restart:${JSON.stringify(selector ?? null)}`); return { client: 'acme', operation: 'restart', state: 'online' }; },
};

const view = render(createElement(OdooTuiRoute, { api }));
await waitFor(() => screen.getByRole('button', { name: /Open acme client panel/ }));

for (const id of ['odoo-tui-route', 'client-selection', 'runtime-status', 'lifecycle-actions', 'module-updates', 'database-summary', 'odoo-logs']) {
  if (!view.container.querySelector(`[data-testid="${id}"]`)) throw new Error(`missing section ${id}`);
}
if (!screen.getByRole('button', { name: /Open acme client panel/ })) throw new Error('client panel is missing');
if (view.container.querySelector('[data-testid="odoo-tui-state-notice"]')) throw new Error('ready state must not show a generic confirmation banner');
if (view.container.querySelector('[data-testid="operation-result"]')) throw new Error('idle state must not show an empty operation panel');
if (screen.queryByText('Environment')) throw new Error('fixed local environment must not be rendered');
if (screen.queryByRole('button', { name: 'Refresh Odoo data' })) throw new Error('normal Instance view must not show a Refresh button');
if (!screen.getByRole('button', { name: 'Stop' })) throw new Error('stop action is missing');
if (!screen.getByRole('button', { name: 'Restart' })) throw new Error('restart action is missing');
if (!(screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled) throw new Error('start action must be disabled while online');
if (!screen.getByRole('button', { name: 'Start' }).className.includes('text-positive')) throw new Error('Start must use positive text styling');
if (!screen.getByRole('button', { name: 'Stop' }).className.includes('text-negative')) throw new Error('Stop must use negative text styling');
if (!screen.getByRole('button', { name: 'Restart' }).className.includes('text-accent')) throw new Error('Restart must use accent text styling');
if (!screen.getByRole('button', { name: 'Restart' }).className.includes('border-border')) throw new Error('lifecycle buttons must use the neutral border style');
if (!screen.getByRole('button', { name: 'Restart' }).className.includes('rounded-[var(--control-radius)]')) throw new Error('lifecycle buttons must use the host rounded style');
if (!screen.getByRole('button', { name: 'Restart' }).querySelector('svg')) throw new Error('lifecycle buttons must render an icon');
if (!view.container.querySelector('[data-testid="client-selection"] [data-testid="runtime-status"]')) throw new Error('runtime status must be inside the selected client card');
if (!screen.getByRole('button', { name: 'Client' })) throw new Error('Client start mode is missing');
if (!screen.getByRole('button', { name: 'Database Manager' })) throw new Error('Database Manager start mode is missing');
if (screen.queryByText('Start as')) throw new Error('start mode label is redundant');
if (screen.queryByText(/Mode:/)) throw new Error('mode summary is redundant');
if (!screen.getByRole('button', { name: 'Client' }).className.includes('nav-link-active')) throw new Error('selected mode must be visibly highlighted');
if (screen.getByRole('button', { name: 'Client' }).className.includes('border-accent')) throw new Error('selected mode must not create a second inner border');
if (!screen.getByRole('button', { name: 'Client' }).querySelector('svg') || !screen.getByRole('button', { name: 'Database Manager' }).querySelector('svg')) throw new Error('mode buttons must render icons');
if (view.container.querySelector('#odoo-client-selector')) throw new Error('native client selector must be removed');
if (!view.container.querySelector('[aria-label="Start mode"] button')?.className.includes('pill pill-button')) throw new Error('start mode buttons must use the host pill pattern');
if (!screen.getByRole('checkbox', { name: 'Select all installed modules' })) throw new Error('ALL module selector is missing');
if (!view.container.querySelector('[data-testid="module-list"] > div > span')?.className.includes('text-accent')) throw new Error('ALL must use accent purple text');
if (screen.queryByText('All installed')) throw new Error('ALL selector must not use an overflowing text label');
await waitFor(() => screen.getByText('Odoo worker ready'));
if (!screen.getByText('Odoo worker ready')) throw new Error('Odoo log entry is missing');
const logArticles = [...view.container.querySelectorAll('[data-testid="odoo-logs"] article')];
if (logArticles.length !== 3) throw new Error('log fixture entries are missing');
const firstLogMeta = logArticles[0].querySelector('div');
if (!firstLogMeta?.textContent?.includes('INFO') || !firstLogMeta.textContent.includes('2026-09-09') || !firstLogMeta.textContent.includes('10:00:00')) throw new Error('log metadata must show level, date, and time');
if (firstLogMeta.textContent.includes('PID') || firstLogMeta.textContent.includes('19_acme') || firstLogMeta.textContent.includes('odoo.modules.loading')) throw new Error('log metadata must not show PID, database, or logger');
for (const article of logArticles) {
  const pills = [...article.querySelectorAll('div > span, div > time')];
  if (pills.length !== 2) throw new Error('each log row must show only level and timestamp pills');
  for (const pill of pills) {
    if (!pill.className.includes('rounded-full') || !pill.className.includes('px-2.5') || !pill.className.includes('py-0.5')) throw new Error('log metadata pills must use the Mission Control Badge style');
  }
}
if (logArticles[0].querySelector('time')?.textContent !== '2026-09-09 10:00:00') throw new Error('timestamp must be rendered as one compact pill');
if (!logArticles[0].querySelector('span')?.className.includes('text-sky-300')) throw new Error('INFO must use the Agents event-badge color');
if (!logArticles[1].querySelector('span')?.className.includes('text-amber-300')) throw new Error('WARNING must retain warning color');
if (!logArticles[2].querySelector('span')?.className.includes('text-rose-300')) throw new Error('ERROR must retain negative color');
const logViewport = view.container.querySelector('[data-testid="odoo-log-viewport"]');
if (!logViewport || !logViewport.className.includes('overflow-y-auto')) throw new Error('Odoo log viewport must scroll');
if (!logViewport.className.includes('100dvh')) throw new Error('Odoo log viewport must use responsive viewport height');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('h-full')) throw new Error('workspace must own page scrolling');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('p-0')) throw new Error('workspace must rely on the host route-stage outer padding');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('overflow-x-hidden')) throw new Error('workspace must contain horizontal overflow');
const allModulesRow = view.container.querySelector('[data-testid="module-list"] > div');
if (!allModulesRow?.className.includes('max-w-full') || !allModulesRow.className.includes('w-full')) throw new Error('ALL row must fit the available width');
if (screen.queryByText('Administrator password')) throw new Error('password panel must not be mounted');
if (screen.queryByText('Operating mode')) throw new Error('mode panel must not be mounted');
const desktopGrid = view.container.querySelector('[data-testid="desktop-workspace-grid"]');
if (!desktopGrid?.className.includes('xl:grid-cols-[minmax(16rem,0.2fr)_minmax(0,0.8fr)]') || !desktopGrid.className.includes('w-full')) throw new Error('desktop workspace must give the log column most of the width and fill its parent');
const desktopLeft = desktopGrid.querySelector('[data-testid="desktop-left-column"]');
if (!desktopLeft?.querySelector('[data-testid="module-updates"]')) throw new Error('desktop module list must be in the left column');
if (desktopGrid.querySelector('[data-testid="odoo-logs"]')?.parentElement !== desktopGrid) throw new Error('desktop logs must occupy the right grid column');
if (desktopGrid.querySelector('[data-testid="database-context"]')) throw new Error('database context must be merged into Instance');
if (!view.container.querySelector('[data-testid="client-selection"] [data-testid="database-summary"]')) throw new Error('database summary must remain inside the selected client card');
if (!view.container.querySelector('[data-testid="desktop-workspace-grid"]')?.className.includes('xl:flex-1') || !view.container.querySelector('[data-testid="desktop-workspace-grid"]')?.className.includes('xl:min-h-0')) throw new Error('desktop workspace must consume only the remaining height');
if (!desktopLeft?.className.includes('xl:h-full') || !desktopLeft.className.includes('xl:flex-col') || !desktopLeft.className.includes('xl:min-h-0')) throw new Error('desktop left area must fill the grid row without owning page scroll');
const modulePanel = desktopGrid.querySelector('[data-testid="module-updates"]');
const clientPanel = view.container.querySelector('[data-testid="client-selection"]');
if (clientPanel?.parentElement !== desktopLeft) throw new Error('client selection must be inside the desktop left column');
if (modulePanel?.parentElement !== clientPanel) throw new Error('Modules must share the single client container');
if (!clientPanel?.className.includes('space-y-4')) throw new Error('client and Modules must use one spaced outer panel');
if (modulePanel?.classList.contains('rounded-lg') || modulePanel?.classList.contains('border')) throw new Error('Modules must not render a second outer panel');
if (!view.container.querySelector('[data-testid="client-accordion"]')?.hasAttribute('inert')) throw new Error('closed client accordion must not expose focusable descendants');
if (!modulePanel?.className.includes('xl:flex-1') || !modulePanel.className.includes('xl:flex-col') || !view.container.querySelector('[data-testid="module-content"]')?.className.includes('xl:flex-1')) throw new Error('Modules must preserve the desktop flex sizing chain');
if (!view.container.querySelector('[data-testid="module-list"]')?.className.includes('xl:flex-1') || !view.container.querySelector('[data-testid="module-list"]')?.className.includes('overflow-y-auto')) throw new Error('desktop module list must own the internal scroll');
if (!desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('w-full') || !desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('xl:h-full') || !desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('xl:min-h-0')) throw new Error('desktop log panel must fill the remaining column width and height');
if (!view.container.querySelector('[data-testid="odoo-log-viewport"]')?.className.includes('xl:max-h-none') || !view.container.querySelector('[data-testid="odoo-log-viewport"]')?.className.includes('xl:flex-1')) throw new Error('desktop log viewport must consume the remaining panel height');
if (!screen.getByText('Lifecycle')) throw new Error('Log operational controls are missing');

const user = userEvent.setup();
const installedModule = screen.getByRole('checkbox', { name: 'Select module base' });
const installedModuleRow = installedModule.closest('article');
if (!installedModuleRow?.className.includes('bg-surface') || installedModuleRow.className.includes('bg-positive/10') || !installedModuleRow.className.includes('border-border-subtle') || installedModuleRow.className.includes('border-positive/40')) throw new Error('installed modules must keep the dark surface without a bright border');
const uninstalledModule = screen.getByRole('checkbox', { name: 'Select module demo' }) as HTMLInputElement;
if (!uninstalledModule.disabled) throw new Error('uninstalled modules must not be selectable');
const uninstalledModuleRow = uninstalledModule.closest('article');
if (!uninstalledModuleRow?.className.includes('opacity-60') || !uninstalledModuleRow.className.includes('cursor-not-allowed')) throw new Error('uninstalled modules must be visibly disabled');
if (screen.queryByText('Installed')) throw new Error('module state must not use an Installed label');
await user.click(screen.getByRole('button', { name: 'Stop' }));
if (screen.queryByTestId('operation-confirmation-dialog')) throw new Error('Stop must not open a confirmation dialog');
await waitFor(() => { if (!calls.includes('stop')) throw new Error('stop was not sent immediately'); });

await user.click(screen.getByRole('button', { name: 'Restart' }));
if (screen.queryByTestId('operation-confirmation-dialog')) throw new Error('Restart must not open a confirmation dialog');
await waitFor(() => { if (!calls.includes('restart:null')) throw new Error('restart was not sent immediately'); });

await user.click(screen.getByRole('checkbox', { name: 'Select module base' }));
await user.click(screen.getByRole('button', { name: 'Restart' }));
await waitFor(() => { if (!calls.includes('restart:{"modules":["base"]}')) throw new Error('selected modules were not sent with restart'); });

await user.click(screen.getByRole('checkbox', { name: 'Select all installed modules' }));
if (!(screen.getByRole('checkbox', { name: 'Select all installed modules' }) as HTMLInputElement).checked) throw new Error('ALL must be selected');
const allRow = view.container.querySelector('[data-testid="module-list"] > div');
if (!allRow?.className.includes('bg-accent-subtle')) throw new Error('ALL row must visibly indicate its selected state');
if ((screen.getByRole('checkbox', { name: 'Select module base' }) as HTMLInputElement).checked) throw new Error('individual modules must clear when ALL is selected');
if (!(screen.getByRole('checkbox', { name: 'Select module base' }) as HTMLInputElement).disabled) throw new Error('individual modules must be disabled when ALL is selected');
await user.click(screen.getByRole('button', { name: 'Restart' }));
await waitFor(() => { if (!calls.includes('restart:{"update_all":true}')) throw new Error('ALL selector was not sent with restart'); });

if (screen.queryByRole('button', { name: 'Update all installed' })) throw new Error('legacy Update All button must be removed');
const acmePanel = screen.getByRole('button', { name: /Open acme client panel/ });
if (acmePanel.getAttribute('aria-expanded') !== 'false' || acmePanel.getAttribute('aria-controls') !== 'client-accordion-list') throw new Error('client panel must control the closed client accordion');
if (!acmePanel.querySelector('svg')) throw new Error('client panel must render the animated chevron');
await user.click(acmePanel);
await waitFor(() => screen.getByRole('button', { name: /Select beta client/ }));
if (view.container.querySelectorAll('[data-testid="client-accordion"] [role="listitem"]').length !== 1) throw new Error('client accordion items must expose listitem semantics');
if (view.container.querySelector('[data-testid="client-accordion"]')?.hasAttribute('inert')) throw new Error('open client accordion must be focusable');
if (!view.container.querySelector('[data-testid="module-updates"]')?.hasAttribute('inert')) throw new Error('hidden Modules must not expose focusable controls');
await user.click(screen.getByRole('button', { name: /Select beta client/ }));
await waitFor(() => screen.getByText('19_beta'));
await new Promise((resolve) => setTimeout(resolve, 350));
if (screen.getByRole('button', { name: /Open beta client panel/ }).getAttribute('aria-expanded') !== 'false') throw new Error('client accordion must close after selecting a client');
if (document.activeElement !== screen.getByRole('button', { name: /Open beta client panel/ })) throw new Error('focus must return to the selected client panel');
if (!view.container.querySelector('[data-testid="module-updates"]')?.className.includes('opacity-100')) throw new Error('Modules must return after client selection');
if (view.container.querySelector('[data-testid="module-updates"]')?.hasAttribute('inert')) throw new Error('visible Modules must be focusable after client selection');
await user.click(screen.getByRole('button', { name: /Open beta client panel/ }));
if (screen.getByRole('button', { name: /Open beta client panel/ }).getAttribute('aria-expanded') !== 'true') throw new Error('selected client panel must reopen its accordion');
view.unmount();

const startModes: string[] = [];
const stoppedApi = {
  ...api,
  getSnapshot: async (client?: string) => client ? { ...(await getSnapshot(client)), snapshot: snapshot(client, 'stopped') } : getSnapshot(),
  getStatus: async () => ({ status: { state: 'stopped' as const, pid: null, process_name: 'odoo-19-acme-local', pm2_id: 1, startup_mode: null } }),
  start: async (_client: string, _confirmation: string, mode?: string) => { startModes.push(mode ?? 'missing'); return { client: 'acme', operation: 'start', state: 'online' }; },
};
const stoppedView = render(createElement(OdooTuiRoute, { api: stoppedApi }));
await waitFor(() => screen.getByRole('button', { name: 'Start' }));
if (!stoppedView.container.querySelector('[data-testid="client-selection"] [data-testid="runtime-status"]')) throw new Error('stopped runtime metadata is missing');
if ((screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled) throw new Error('Start must be enabled while stopped');
if (!(screen.getByRole('button', { name: 'Stop' }) as HTMLButtonElement).disabled) throw new Error('Stop must be disabled while stopped');
await user.click(screen.getByRole('button', { name: 'Database Manager' }));
if (!(screen.getByRole('button', { name: 'Database Manager' }) as HTMLButtonElement).getAttribute('aria-pressed') || !(screen.getByRole('button', { name: 'Database Manager' }) as HTMLButtonElement).getAttribute('aria-pressed')!.includes('true')) throw new Error('Database Manager mode was not selected');
if ((screen.getByRole('button', { name: 'Client' }) as HTMLButtonElement).getAttribute('aria-pressed') !== 'false') throw new Error('Client mode must be deselected when Database Manager is selected');
if (!(screen.getByRole('checkbox', { name: 'Select module base' }) as HTMLInputElement).disabled) throw new Error('modules must be disabled in Database Manager mode');
await user.click(screen.getByRole('button', { name: 'Start' }));
await waitFor(() => { if (!startModes.includes('database_manager')) throw new Error('selected start mode was not sent'); });
stoppedView.unmount();
console.log('UI mounted route contract PASS');

let delayBetaIdentity = false;
const betaIdentityResolvers: Array<() => void> = [];
const betaIdentityGate = new Promise<void>((resolve) => { betaIdentityResolvers.push(resolve); });
const snapshotApi = {
  listClients: async () => ({ clients: [{ name: 'acme', release: '19', environment: 'local', local_url: null }, { name: 'beta', release: '19', environment: 'local', local_url: null }] }),
  listReleases: async () => ({ releases: [{ version: '19' }] }),
  getIdentity: async (client = 'acme') => {
    if (client === 'beta' && delayBetaIdentity) await betaIdentityGate;
    return { instance: { client, release: '19', environment: 'local', config_identity: `/private/${client}.conf`, database: `19_${client}`, http_port: client === 'beta' ? 8070 : 8069, longpolling_port: client === 'beta' ? 8073 : 8072 } };
  },
  getStatus: async (client = 'acme') => ({ status: { state: client === 'beta' ? 'stopped' as const : 'online' as const, pid: client === 'beta' ? null : 123, process_name: `odoo-19-${client}-local`, pm2_id: client === 'beta' ? 2 : 1, startup_mode: null } }),
  getControl: async () => ({ control: { control_mode: 'client' as const, lifecycle_eligible: true, reason: null } }),
  getModules: async (client = 'acme') => ({ client, modules: [{ name: 'base', version: '1.0', installed: true, installable: true, update_available: false, dependencies: [] }] }),
  getDatabases: async (client = 'acme') => ({ client, databases: [{ name: `19_${client}`, exists: true }] }),
  getLogs: async () => ({ entries: [], next_cursor: null, has_more: false, cursor_reset: false }),
  start: async () => ({}),
  stop: async () => ({}),
  restart: async () => ({}),
};
const snapshotView = render(createElement(OdooTuiRoute, { api: snapshotApi }));
await waitFor(() => screen.getAllByText('19_acme')[0]);
if (!screen.getByTestId('odoo-log-empty-state')) throw new Error('empty log state is missing');
await user.click(screen.getByRole('button', { name: /Open acme client panel/ }));
await waitFor(() => screen.getByRole('button', { name: /Select beta client/ }));
await user.click(screen.getByRole('button', { name: /Select beta client/ }));
await waitFor(() => screen.getByRole('button', { name: /Open beta client panel/ }));
if (screen.queryByText('/private/beta.conf')) throw new Error('config_identity must not reach the rendered workspace');
await user.click(screen.getByRole('button', { name: /Open beta client panel/ }));
await waitFor(() => screen.getByRole('button', { name: /Select acme client/ }));
await user.click(screen.getByRole('button', { name: /Select acme client/ }));
await waitFor(() => screen.getByRole('button', { name: /Open acme client panel/ }));
delayBetaIdentity = true;
await user.click(screen.getByRole('button', { name: /Open acme client panel/ }));
await waitFor(() => screen.getByRole('button', { name: /Select beta client/ }));
await user.click(screen.getByRole('button', { name: /Select beta client/ }));
await waitFor(() => screen.getByRole('button', { name: /Open beta client panel/ }));
if (!screen.getByRole('button', { name: /Open beta client panel/ })) throw new Error('cached beta snapshot was cleared during refresh');
const betaAccessibleName = screen.getByRole('button', { name: /Open beta client panel/ }).getAttribute('aria-label') || '';
if (!betaAccessibleName.includes('database 19_beta') || !betaAccessibleName.includes('process odoo-19-beta-local')) throw new Error('cached beta identity/status details were cleared during refresh');
if (screen.queryByTestId('odoo-tui-state-notice')) throw new Error('cached refresh should not replace ready workspace with loading notice');
betaIdentityResolvers[0]?.();
await waitFor(() => screen.getAllByText('19_beta')[0]);
snapshotView.unmount();
console.log('per-client snapshot preservation PASS');

let betaOnline = false;
let databaseAvailable = false;
const statusPollCalls: string[] = [];
const databasePollCalls: boolean[] = [];
const pollingApi = {
  ...snapshotApi,
  getStatus: async (client = 'acme') => {
    statusPollCalls.push(client);
    const online = client === 'beta' ? betaOnline : true;
    return { status: { state: online ? 'online' as const : 'stopped' as const, pid: online ? 123 : null, process_name: `odoo-19-${client}-local`, pm2_id: client === 'beta' ? 2 : 1, startup_mode: null } };
  },
  getDatabases: async (client = 'acme') => {
    databasePollCalls.push(databaseAvailable);
    return { client, databases: [{ name: `19_${client}`, exists: client === 'acme' ? databaseAvailable : true }] };
  },
};
const originalSetInterval = window.setInterval;
const originalSetTimeout = window.setTimeout;
window.setInterval = ((handler: Parameters<typeof window.setInterval>[0]) => originalSetInterval(handler, 10)) as typeof window.setInterval;
window.setTimeout = ((handler: Parameters<typeof window.setTimeout>[0], timeout?: number) => originalSetTimeout(handler, timeout && timeout >= 3000 ? 10 : timeout)) as typeof window.setTimeout;
const pollingView = render(createElement(OdooTuiRoute, { api: pollingApi }));
await new Promise((resolve) => setTimeout(resolve, 80));
if (!statusPollCalls.includes('beta')) throw new Error('background polling did not refresh beta status before selector open');
if (databasePollCalls.filter((available) => available === false).length === 0) throw new Error('database baseline was not read');
betaOnline = true;
databaseAvailable = true;
await waitFor(() => {
  if (!statusPollCalls.includes('beta') || !databasePollCalls.includes(true)) throw new Error('background polling did not observe external state changes');
});
pollingView.unmount();
window.setInterval = originalSetInterval;
window.setTimeout = originalSetTimeout;
console.log('background runtime polling PASS');

const snapshotRequests: Array<string | undefined> = [];
const snapshotEnvelope = (client?: string) => client ? {
  protocol_version: 1 as const,
  process_instance_id: '0123456789abcdef0123456789abcdef',
  registry_epoch: 0,
  registry_identity: '[["acme","[\\"acme\\",\\"19\\",\\"local\\",null]"]]',
  releases: [{ version: '19' }],
  releases_error: null,
  client: 'acme',
  snapshot: {
    registry_identity: '["acme","19","local",null]',
    identity: { client: 'acme', release: '19', environment: 'local', database: '19_acme', http_port: 8069, longpolling_port: 8072 },
    status: { state: 'online' as const, pid: 123, process_name: 'odoo-19-acme-local', pm2_id: 1, startup_mode: null },
    control: { control_mode: 'client' as const, lifecycle_eligible: true, reason: null },
    modules: [{ name: 'base', version: '1.0', installed: true, installable: true, update_available: false, dependencies: [] }],
    databases: [{ name: '19_acme', exists: true }],
  },
  errors: {},
} : {
  protocol_version: 1 as const,
  process_instance_id: '0123456789abcdef0123456789abcdef',
  registry_epoch: 0,
  registry_identity: '[["acme","[\\"acme\\",\\"19\\",\\"local\\",null]"]]',
  clients: [{ name: 'acme', release: '19', environment: 'local', local_url: null, registry_identity: '["acme","19","local",null]' }],
  releases: [{ version: '19' }],
  releases_error: null,
  errors: {},
};
const backendSnapshotApi = {
  ...api,
  getSnapshot: async (client?: string) => { snapshotRequests.push(client); return snapshotEnvelope(client); },
};
const backendSnapshotView = render(createElement(OdooTuiRoute, { api: backendSnapshotApi }));
await new Promise((resolve) => setTimeout(resolve, 20));
await waitFor(() => { if (screen.queryAllByText('19_acme').length === 0) throw new Error('snapshot client data was not rendered'); });
if (snapshotRequests[0] !== undefined || snapshotRequests[1] !== 'acme') throw new Error('snapshot API request topology is incorrect');
if (screen.queryByText('/private/acme.conf')) throw new Error('snapshot identity leaked config data');
backendSnapshotView.unmount();
console.log('snapshot consumer PASS');

let initialCollectionBusy = true;
const initialBusyApi: typeof backendSnapshotApi = {
  ...backendSnapshotApi,
  getSnapshot: async (client?: string) => {
    if (!client && initialCollectionBusy) {
      initialCollectionBusy = false;
      const error = new Error('busy') as Error & { code: string };
      error.code = 'SNAPSHOT_BUSY';
      throw error;
    }
    return snapshotEnvelope(client);
  },
};
const initialBusyView = render(createElement(OdooTuiRoute, { api: initialBusyApi }));
await waitFor(() => { if (screen.queryAllByText('19_acme').length === 0) throw new Error('initial collection busy did not retry'); }, { timeout: 2000 });
initialBusyView.unmount();
console.log('initial collection backpressure PASS');
