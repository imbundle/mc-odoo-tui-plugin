import type { OdooApi, OdooClient, OdooControl, OdooDatabase, OdooIdentity, OdooLogPage, OdooModule, OdooMutationPrecondition, OdooSnapshotEnvelope, OdooStatus } from './types';

type Json = Record<string, unknown>;
type FetchLike = typeof fetch;
const REQUEST_TIMEOUT_MS = 6000;

function token(): string {
  try {
    const stored = window.localStorage.getItem('mission-control-token')?.trim() || '';
    if (stored) return stored;
  } catch {
    // No persistent token is available; let the host return 401.
  }
  return '';
}

function errorCode(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return undefined;
  const value = payload as Record<string, unknown>;
  return typeof value.error === 'string' ? value.error : typeof value.code === 'string' ? value.code : undefined;
}

function errorMessage(_payload: unknown): string {
  return 'Odoo backend request failed.';
}

export function createOdooApi(fetcher: FetchLike = fetch): OdooApi {
  async function request<T>(path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
    const bearer = token();
    const deadline = new AbortController();
    let rejectDeadline!: (error: Error) => void;
    const deadlinePromise = new Promise<never>((_, reject) => { rejectDeadline = reject; });
    const timeout = globalThis.setTimeout(() => {
      deadline.abort();
      rejectDeadline(new Error('Odoo backend request timed out.'));
    }, timeoutMs);
    const abortCaller = () => {
      deadline.abort();
      rejectDeadline(new Error('Odoo backend request was aborted.'));
    };
    if (init?.signal?.aborted) abortCaller();
    else init?.signal?.addEventListener('abort', abortCaller, { once: true });
    try {
      const response = await Promise.race([
        fetcher(`/api/local/odoo-tui/${path}`, {
          ...init,
          signal: deadline.signal,
          headers: {
            Accept: 'application/json',
            ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
            ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
            ...init?.headers,
          },
        }),
        deadlinePromise,
      ]);
      const payload = await Promise.race([
        response.json().catch(() => null),
        deadlinePromise,
      ]);
      if (!response.ok) {
        const error = new Error(errorMessage(payload)) as Error & { code?: string; status?: number };
        error.code = errorCode(payload);
        error.status = response.status;
        throw error;
      }
      if (!payload || typeof payload !== 'object') throw new Error('Odoo backend returned an invalid response.');
      return payload as T;
    } finally {
      globalThis.clearTimeout(timeout);
      init?.signal?.removeEventListener('abort', abortCaller);
    }
  }
  const get = <T>(path: string, signal?: AbortSignal, timeoutMs = REQUEST_TIMEOUT_MS) => request<T>(path, signal ? { signal } : undefined, timeoutMs);
  const post = <T>(path: string, body: unknown, signal?: AbortSignal) => request<T>(path, { method: 'POST', body: JSON.stringify(body), ...(signal ? { signal } : {}) });
  const query = (client: string) => `?client=${encodeURIComponent(client)}`;
  return {
    getSnapshot: (client, signal) => get<OdooSnapshotEnvelope>(`snapshot${client ? query(client) : ''}`, signal, 13000),
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
    start: (client, confirmation, mode = 'client', signal, precondition?: OdooMutationPrecondition) => post(`instance/start${query(client)}`, { confirmation, mode, ...(precondition ? { precondition } : {}) }, signal),
    stop: (client, confirmation, signal, precondition?: OdooMutationPrecondition) => post(`instance/stop${query(client)}`, { confirmation, ...(precondition ? { precondition } : {}) }, signal),
    restart: (client, confirmation, selector, mode = 'client', signal, precondition?: OdooMutationPrecondition) => post(`instance/restart${query(client)}`, { ...(selector ?? {}), confirmation, mode, ...(precondition ? { precondition } : {}) }, signal),
  };
}
