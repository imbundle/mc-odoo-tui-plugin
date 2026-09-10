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
const api = {
  listClients: async () => ({ clients: [{ name: 'acme', release: '19', environment: 'local', local_url: null }, { name: 'beta', release: '19', environment: 'local', local_url: null }] }),
  listReleases: async () => ({ releases: [{ version: '19' }] }),
  getIdentity: async () => ({ instance: { client: 'acme', release: '19', environment: 'local', config_identity: null, database: '19_acme', http_port: 8069, longpolling_port: 8072 } }),
  getStatus: async (client = 'acme') => ({ status: { state: client === 'beta' ? 'stopped' as const : 'online' as const, pid: client === 'beta' ? null : 123, process_name: `odoo-19-${client}-local`, pm2_id: client === 'beta' ? 2 : 1, startup_mode: null } }),
  getControl: async () => ({ control: { control_mode: 'client', lifecycle_eligible: true, reason: null } }),
  getModules: async () => ({ client: 'acme', modules: [{ name: 'base', version: '1.0', installed: true, installable: true, update_available: false, dependencies: [] }, { name: 'demo', version: '1.0', installed: false, installable: true, update_available: false, dependencies: [] }] }),
  getDatabases: async () => ({ client: 'acme', databases: [{ name: '19_acme', exists: true }] }),
  getLogs: async () => ({ entries: [{ timestamp: '2026-09-09T10:00:00Z', pid: 123, level: 'INFO', database: '19_acme', logger: 'odoo.modules.loading', message: 'Odoo worker ready' }], next_cursor: 'cursor', has_more: false, cursor_reset: false }),
  start: async () => { calls.push('start'); return { client: 'acme', operation: 'start', state: 'online' }; },
  stop: async () => { calls.push('stop'); return { client: 'acme', operation: 'stop', state: 'stopped' }; },
  restart: async (_client: string, _confirmation: string, selector?: { modules: string[] } | { update_all: true }) => { calls.push(`restart:${JSON.stringify(selector ?? null)}`); return { client: 'acme', operation: 'restart', state: 'online' }; },
};

const view = render(createElement(OdooTuiRoute, { api }));
await waitFor(() => screen.getByLabelText('Registered Odoo client'));

for (const id of ['odoo-tui-route', 'client-selection', 'runtime-status', 'lifecycle-actions', 'module-updates', 'database-summary', 'odoo-logs']) {
  if (!view.container.querySelector(`[data-testid="${id}"]`)) throw new Error(`missing section ${id}`);
}
if (!screen.getByLabelText('Registered Odoo client')) throw new Error('client selector is missing');
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
if (!view.container.querySelector('[data-testid="client-selection"] [data-testid="runtime-status"]')) throw new Error('runtime status must be inside Instance');
if (!view.container.querySelector('[data-testid="runtime-status-dot"]')?.className.includes('animate-pulse')) throw new Error('online status dot must pulse');
if (screen.queryByText('Online')) throw new Error('runtime state text should be represented by the status dot');
if (!screen.getByRole('button', { name: 'Client' })) throw new Error('Client start mode is missing');
if (!screen.getByRole('button', { name: 'Database Manager' })) throw new Error('Database Manager start mode is missing');
if (screen.queryByText('Start as')) throw new Error('start mode label is redundant');
if (screen.queryByText(/Mode:/)) throw new Error('mode summary is redundant');
if (!screen.getByRole('button', { name: 'Client' }).className.includes('nav-link-active')) throw new Error('selected mode must be visibly highlighted');
if (screen.getByRole('button', { name: 'Client' }).className.includes('border-accent')) throw new Error('selected mode must not create a second inner border');
if (!screen.getByRole('button', { name: 'Client' }).querySelector('svg') || !screen.getByRole('button', { name: 'Database Manager' }).querySelector('svg')) throw new Error('mode buttons must render icons');
if (!view.container.querySelector('#odoo-client-selector')?.className.includes('h-11')) throw new Error('client selector height is not normalized');
if (!view.container.querySelector('[aria-label="Start mode"] button')?.className.includes('pill pill-button')) throw new Error('start mode buttons must use the host pill pattern');
if (!screen.getByRole('checkbox', { name: 'Select all installed modules' })) throw new Error('ALL module selector is missing');
if (!view.container.querySelector('label[title="Update all installed modules"] > span')?.className.includes('text-accent')) throw new Error('ALL must use accent purple text');
if (screen.queryByText('All installed')) throw new Error('ALL selector must not use an overflowing text label');
if (!screen.getByText('Odoo worker ready')) throw new Error('Odoo log entry is missing');
const logViewport = view.container.querySelector('[data-testid="odoo-log-viewport"]');
if (!logViewport || !logViewport.className.includes('overflow-y-auto')) throw new Error('Odoo log viewport must scroll');
if (!logViewport.className.includes('100dvh')) throw new Error('Odoo log viewport must use responsive viewport height');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('h-full')) throw new Error('workspace must own page scrolling');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('p-0')) throw new Error('workspace must rely on the host route-stage outer padding');
if (!view.container.querySelector('main[data-testid="odoo-tui-route"]')?.className.includes('overflow-x-hidden')) throw new Error('workspace must contain horizontal overflow');
const allModulesRow = view.container.querySelector('label[title="Update all installed modules"]');
if (!allModulesRow?.className.includes('max-w-full') || !allModulesRow.className.includes('w-full')) throw new Error('ALL row must fit the available width');
if (screen.queryByText('Administrator password')) throw new Error('password panel must not be mounted');
if (screen.queryByText('Operating mode')) throw new Error('mode panel must not be mounted');
const desktopGrid = view.container.querySelector('[data-testid="desktop-workspace-grid"]');
if (!desktopGrid?.className.includes('xl:grid-cols-[minmax(16rem,0.2fr)_minmax(0,0.8fr)]') || !desktopGrid.className.includes('w-full')) throw new Error('desktop workspace must give the log column most of the width and fill its parent');
if (view.container.querySelector('[data-testid="client-selection"]')?.nextElementSibling !== desktopGrid) throw new Error('Instance must remain above the desktop workspace grid');
const desktopLeft = desktopGrid.querySelector('[data-testid="desktop-left-column"]');
if (!desktopLeft?.querySelector('[data-testid="module-updates"]')) throw new Error('desktop module list must be in the left column');
if (desktopGrid.querySelector('[data-testid="odoo-logs"]')?.parentElement !== desktopGrid) throw new Error('desktop logs must occupy the right grid column');
if (desktopGrid.querySelector('[data-testid="database-context"]')) throw new Error('database context must be merged into Instance');
if (!view.container.querySelector('[data-testid="client-selection"] [data-testid="database-summary"]')) throw new Error('database summary must remain inside Instance');
if (!view.container.querySelector('[data-testid="desktop-workspace-grid"]')?.className.includes('xl:flex-1') || !view.container.querySelector('[data-testid="desktop-workspace-grid"]')?.className.includes('xl:min-h-0')) throw new Error('desktop workspace must consume only the remaining height');
if (!desktopLeft?.className.includes('xl:h-full') || !desktopLeft.className.includes('xl:flex-col') || !desktopLeft.className.includes('xl:min-h-0')) throw new Error('desktop left area must fill the grid row without owning page scroll');
const modulePanel = desktopGrid.querySelector('[data-testid="module-updates"]');
if (!modulePanel?.className.includes('xl:flex-1') || !modulePanel.className.includes('xl:flex-col') || !modulePanel.className.includes('xl:min-h-0')) throw new Error('desktop module area must consume the remaining left-column height');
if (!view.container.querySelector('[data-testid="module-list"]')?.className.includes('xl:flex-1') || !view.container.querySelector('[data-testid="module-list"]')?.className.includes('xl:overflow-y-auto')) throw new Error('desktop module list must own the internal scroll');
if (!desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('w-full') || !desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('xl:h-full') || !desktopGrid.querySelector('[data-testid="odoo-logs"]')?.className.includes('xl:min-h-0')) throw new Error('desktop log panel must fill the remaining column width and height');
if (!view.container.querySelector('[data-testid="odoo-log-viewport"]')?.className.includes('xl:max-h-none') || !view.container.querySelector('[data-testid="odoo-log-viewport"]')?.className.includes('xl:flex-1')) throw new Error('desktop log viewport must consume the remaining panel height');
if (!screen.getByText('Log')) throw new Error('Odoo log panel is missing');

const user = userEvent.setup();
const installedModule = screen.getByRole('checkbox', { name: 'Select module base' });
const installedModuleRow = installedModule.closest('label');
if (!installedModuleRow?.className.includes('bg-surface') || installedModuleRow.className.includes('bg-positive/10') || !installedModuleRow.className.includes('border-transparent') || installedModuleRow.className.includes('border-positive/40')) throw new Error('installed modules must keep the dark surface without a bright border');
const uninstalledModule = screen.getByRole('checkbox', { name: 'Select module demo' }) as HTMLInputElement;
if (!uninstalledModule.disabled) throw new Error('uninstalled modules must not be selectable');
const uninstalledModuleRow = uninstalledModule.closest('label');
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
const allRow = view.container.querySelector('label[title="Update all installed modules"]');
if (!allRow?.className.includes('border-accent/50') || !allRow.className.includes('bg-accent-subtle')) throw new Error('ALL row must visibly indicate its selected state');
if ((screen.getByRole('checkbox', { name: 'Select module base' }) as HTMLInputElement).checked) throw new Error('individual modules must clear when ALL is selected');
if (!(screen.getByRole('checkbox', { name: 'Select module base' }) as HTMLInputElement).disabled) throw new Error('individual modules must be disabled when ALL is selected');
await user.click(screen.getByRole('button', { name: 'Restart' }));
await waitFor(() => { if (!calls.includes('restart:{"update_all":true}')) throw new Error('ALL selector was not sent with restart'); });

if (screen.queryByRole('button', { name: 'Update all installed' })) throw new Error('legacy Update All button must be removed');
await user.click(screen.getByRole('button', { name: 'Registered Odoo client' }));
await waitFor(() => screen.getByRole('option', { name: /beta/ }));
if (!screen.getByRole('option', { name: /beta.*Stopped/ })) throw new Error('client accordion must show per-client runtime state');
await user.click(screen.getByRole('option', { name: /beta/ }));
if (screen.getByRole('button', { name: 'Registered Odoo client' }).getAttribute('aria-expanded') !== 'false') throw new Error('client accordion must close after selection');
if (!screen.getByRole('button', { name: 'Registered Odoo client' }).textContent?.includes('beta')) throw new Error('client selection did not update');
view.unmount();

const startModes: string[] = [];
const stoppedApi = {
  ...api,
  getStatus: async () => ({ status: { state: 'stopped' as const, pid: null, process_name: 'odoo-19-acme-local', pm2_id: 1, startup_mode: null } }),
  start: async (_client: string, _confirmation: string, mode?: string) => { startModes.push(mode ?? 'missing'); return { client: 'acme', operation: 'start', state: 'online' }; },
};
const stoppedView = render(createElement(OdooTuiRoute, { api: stoppedApi }));
await waitFor(() => screen.getByRole('button', { name: 'Start' }));
if (!stoppedView.container.querySelector('[data-testid="runtime-status-dot"]')?.className.includes('bg-text-muted')) throw new Error('stopped status dot must be gray');
if (stoppedView.container.querySelector('[data-testid="runtime-status-dot"]')?.className.includes('animate-pulse')) throw new Error('stopped status dot must not pulse');
if (stoppedView.container.querySelector('[role="img"][aria-label="Stopped"]') === null) throw new Error('stopped status label must remain accessible');
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
