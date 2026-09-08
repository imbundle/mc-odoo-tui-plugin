import type { MCPluginManifest } from '../../core/plugins/types';

const manifest: MCPluginManifest = {
  id: 'odoo-tui',
  name: 'Odoo TUI',
  description: 'Mission Control plugin smoke test for the Odoo TUI integration.',
  version: '0.1.0',
  enabled: true,
  routePath: '/odoo-tui',
  navItem: {
    to: '/odoo-tui',
    label: 'Odoo TUI',
    icon: 'PackageOpen',
    order: 70,
  },
};

export default manifest;
