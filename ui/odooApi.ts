import type { OdooApi, OdooClient, OdooControl, OdooDatabase, OdooIdentity, OdooLogPage, OdooModule, OdooStatus } from './types';

type Json = Record<string, unknown>;
type FetchLike = typeof fetch;

function token(): string {
  try {
    return window.localStorage.getItem('mission-control-token')?.trim() || '';
  } catch {
    return '';
  }
}

function errorMessage(payload: unknown): string {
  if (payload && typeof payload === 'object' && typeof (payload as Json).detail === 'string') return String((payload as Json).detail);
  return 'Odoo backend request failed.';
}

export function createOdooApi(fetcher: FetchLike = fetch): OdooApi {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const bearer = token();
    const response = await fetcher(`/api/local/odoo-tui/${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorMessage(payload));
    if (!payload || typeof payload !== 'object') throw new Error('Odoo backend returned an invalid response.');
    return payload as T;
  }
  const get = <T>(path: string, signal?: AbortSignal) => request<T>(path, signal ? { signal } : undefined);
  const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });
  const query = (client: string) => `?client=${encodeURIComponent(client)}`;
  return {
    listClients: (signal) => get<{ clients: OdooClient[] }>('clients', signal),
    listReleases: (signal) => get<{ releases: { version: string }[] }>('releases', signal),
    getIdentity: (client, signal) => get<{ instance: OdooIdentity }>(`instance/identity${query(client)}`, signal),
    getStatus: (client, signal) => get<{ status: OdooStatus }>(`instance/status${query(client)}`, signal),
    getControl: (client, signal) => get<{ control: OdooControl }>(`instance/control${query(client)}`, signal),
    getModules: (client, signal) => get<{ client: string; modules: OdooModule[] }>(`instance/modules${query(client)}`, signal),
    getDatabases: (client, signal) => get<{ client: string; databases: OdooDatabase[] }>(`instance/databases${query(client)}`, signal),
    getLogs: (client, options = {}) => {
      const params = new URLSearchParams({ client, limit: String(options.limit ?? 100) });
      if (options.cursor) params.set('cursor', options.cursor);
      if (options.direction) params.set('direction', options.direction);
      return get<OdooLogPage>(`instance/logs?${params.toString()}`, options.signal);
    },
    start: (client, confirmation, mode = 'client') => post(`instance/start${query(client)}`, { confirmation, mode }),
    stop: (client, confirmation) => post(`instance/stop${query(client)}`, { confirmation }),
    restart: (client, confirmation, selector, mode = 'client') => post(`instance/restart${query(client)}`, { ...(selector ?? {}), confirmation, mode }),
  };
}
