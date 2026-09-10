import type { PluginManifest } from './types';

const manifest: PluginManifest = {
  id: 'odoo-tui',
  name: 'odoo',
  description: 'Mission Control workspace for confirmed Odoo client and runtime state.',
  version: '0.1.0',
  enabled: true,
  routePath: '/odoo-tui',
  navItem: { to: '/odoo-tui', label: 'odoo', icon: 'PackageOpen', order: 70 },
  endpoints: [
    { method: 'GET', path: '/odoo-tui/clients', handler: 'listClients', authRequired: true },
    { method: 'GET', path: '/odoo-tui/releases', handler: 'listReleases', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/identity', handler: 'getInstanceIdentity', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/status', handler: 'getInstanceStatus', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/control', handler: 'getInstanceControl', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/modules', handler: 'getModules', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/databases', handler: 'getDatabases', authRequired: true },
    { method: 'GET', path: '/odoo-tui/instance/logs', handler: 'getLogs', authRequired: true },
    { method: 'POST', path: '/odoo-tui/instance/start', handler: 'startInstance', authRequired: true },
    { method: 'POST', path: '/odoo-tui/instance/stop', handler: 'stopInstance', authRequired: true },
    { method: 'POST', path: '/odoo-tui/instance/restart', handler: 'restartInstance', authRequired: true },
  ],
};

export default manifest;
