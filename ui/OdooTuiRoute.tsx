import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createOdooApi } from './odooApi';
import { OdooWorkspace, type LifecycleOperation, type WorkspaceState } from './OdooWorkspace';
import type { OdooApi, OdooClient, OdooControl, OdooDatabase, OdooIdentity, OdooLogPage, OdooModule, OdooSnapshotEnvelope, OdooStatus, OdooWorkspaceData, RuntimeState, SafeOdooIdentity, StartMode } from './types';

const MAX_CLIENTS = 32;
const MAX_MODULES = 500;
const MAX_DATABASES = 64;
const MAX_RELEASES = 64;
const MAX_DEPENDENCIES = 128;
const MAX_STRING_BYTES = 512;
const MAX_REGISTRY_IDENTITY_BYTES = 4096;
const MAX_LOG_ENTRIES = 100;
const SNAPSHOT_ERROR_CODES = new Set(['REGISTRY_UNAVAILABLE', 'RELEASES_UNAVAILABLE', 'IDENTITY_UNAVAILABLE', 'STATUS_UNAVAILABLE', 'CONTROL_UNAVAILABLE', 'MODULES_UNAVAILABLE', 'DATABASES_UNAVAILABLE', 'MALFORMED_BACKEND_RESPONSE', 'SNAPSHOT_TIMEOUT', 'SNAPSHOT_UNAVAILABLE', 'SNAPSHOT_TOO_LARGE', 'SNAPSHOT_BUSY']);
type CapabilityName = 'releases' | 'registry' | 'identity' | 'status' | 'control' | 'databases' | 'modules' | 'logs';
type CapabilityMeta = { observedAt: number; stale: boolean; generation: number };
type ClientSyncMeta = { capabilities: Partial<Record<CapabilityName, CapabilityMeta>>; mutationInvalidated: boolean };
type PollBackoff = { delay: number; nextAllowed: number };
type InFlightRead = { promise: Promise<unknown>; controller: AbortController; subscribers: number };
const CAPABILITY_TTL: Record<CapabilityName, number> = {
  identity: 90000,
  status: 90000,
  control: 90000,
  databases: 90000,
  modules: 90000,
  logs: 10000,
  releases: 90000,
  registry: 60000,
};

export interface OdooTuiRouteProps {
  api?: OdooApi;
}

function errorCode(error: unknown): string | undefined {
  return error instanceof Error && typeof (error as Error & { code?: unknown }).code === 'string'
    ? (error as Error & { code: string }).code : undefined;
}

function isSnapshotBusy(error: unknown): boolean {
  return errorCode(error) === 'SNAPSHOT_BUSY';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isNonEmptyString(value);
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isInteger(value) && value >= 0);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function withinStringLimit(value: unknown): boolean {
  return typeof value !== 'string' || value.length === 0 || new TextEncoder().encode(value).length <= MAX_STRING_BYTES;
}

function withinByteLimit(value: unknown, limit: number): boolean {
  return typeof value === 'string' && new TextEncoder().encode(value).length <= limit;
}

function withinNullableByteLimit(value: unknown, limit: number): boolean {
  return value === null || withinByteLimit(value, limit);
}

function validClient(value: unknown): value is OdooClient {
  const name = typeof value === 'object' && value !== null ? (value as Record<string, unknown>).name : undefined;
  return isRecord(value) && Object.keys(value).sort().join(',') === 'environment,local_url,name,release' && isNonEmptyString(name) && withinStringLimit(name) && /^[a-z0-9][a-z0-9_]*$/.test(name)
    && !['__proto__', 'constructor', 'prototype'].includes(name)
    && isNullableString((value as Record<string, unknown>).release)
    && isNullableString((value as Record<string, unknown>).environment)
    && isNullableString((value as Record<string, unknown>).local_url)
    && withinStringLimit(value.release) && withinStringLimit(value.environment) && withinStringLimit(value.local_url);}

function validClients(value: unknown): value is OdooClient[] {
  return Array.isArray(value) && value.length <= MAX_CLIENTS && value.every(validClient)
    && new Set(value.map((client) => client.name)).size === value.length;
}

function validReleases(value: unknown): value is { version: string }[] {
  return Array.isArray(value) && value.length <= MAX_RELEASES && value.every((item) => isRecord(item) && Object.keys(item).length === 1 && isNonEmptyString(item.version) && withinStringLimit(item.version));
}

function validIdentity(value: unknown): value is OdooIdentity {
  return isRecord(value) && Object.keys(value).sort().join(',') === 'client,config_identity,database,environment,http_port,longpolling_port,release'
    && isNonEmptyString(value.client) && withinStringLimit(value.client) && isNonEmptyString(value.release) && withinStringLimit(value.release)
    && value.environment === 'local' && isNullableString(value.config_identity) && withinStringLimit(value.config_identity)
    && isNullableString(value.database) && withinStringLimit(value.database) && isNullableNumber(value.http_port) && isNullableNumber(value.longpolling_port);
}

function validStatus(value: unknown): value is OdooStatus {
  return isRecord(value) && Object.keys(value).sort().join(',') === 'pid,pm2_id,process_name,startup_mode,state' && ['online', 'stopped', 'errored', 'unknown'].includes(String(value.state)) && withinStringLimit(value.process_name) && withinStringLimit(value.startup_mode)
    && isNullableNumber(value.pid) && isNullableString(value.process_name) && isNullableNumber(value.pm2_id) && isNullableString(value.startup_mode);
}

function validControl(value: unknown): value is OdooControl {
  return isRecord(value) && Object.keys(value).sort().join(',') === 'control_mode,lifecycle_eligible,reason' && ['client', 'database_manager', 'unknown'].includes(String(value.control_mode))
    && typeof value.lifecycle_eligible === 'boolean' && isNullableString(value.reason)
    && (value.lifecycle_eligible === false || value.control_mode === 'client')
    && withinStringLimit(value.reason)
    && (value.control_mode === 'client' ? value.lifecycle_eligible === true && value.reason === null : value.control_mode === 'database_manager' ? value.lifecycle_eligible === false && value.reason === 'Database Manager mode is not controllable here' : value.lifecycle_eligible === false && value.reason === 'Runtime identity could not be verified');
}

function validModules(value: unknown): value is OdooModule[] {
  return Array.isArray(value) && value.length <= MAX_MODULES && value.every((item) => isRecord(item)
    && Object.keys(item).every((key) => ['dependencies', 'display_name', 'installable', 'installed', 'name', 'update_available', 'version'].includes(key))
    && ['dependencies', 'installable', 'installed', 'name', 'update_available', 'version'].every((key) => Object.prototype.hasOwnProperty.call(item, key))
    && isNonEmptyString(item.name) && withinStringLimit(item.name) && (item.display_name === undefined || (isNonEmptyString(item.display_name) && withinStringLimit(item.display_name)))
    && isNullableString(item.version) && withinStringLimit(item.version) && typeof item.installed === 'boolean'
    && (item.installable === null || typeof item.installable === 'boolean') && typeof item.update_available === 'boolean'
    && Array.isArray(item.dependencies) && item.dependencies.length <= MAX_DEPENDENCIES && item.dependencies.every((dep) => isNonEmptyString(dep) && withinStringLimit(dep)))
    && new Set(value.map((item) => item.name)).size === value.length;}

function validDatabases(value: unknown): value is OdooDatabase[] {
  return Array.isArray(value) && value.length <= MAX_DATABASES && value.every((item) => isRecord(item) && Object.keys(item).sort().join(',') === 'exists,name' && isNonEmptyString(item.name) && withinStringLimit(item.name) && typeof item.exists === 'boolean')
    && new Set(value.map((item) => item.name)).size === value.length;
}

function validSnapshotError(value: unknown): value is { code: string } {
  return isRecord(value) && Object.keys(value).length === 1 && isNonEmptyString(value.code) && SNAPSHOT_ERROR_CODES.has(value.code);
}

function validSnapshotErrors(value: unknown): value is Record<string, Record<string, { code: string }>> {
  const capabilities = new Set(['identity', 'status', 'control', 'modules', 'databases']);
  return isRecord(value) && Object.entries(value).every(([client, clientErrors]) => /^[a-z0-9][a-z0-9_]*$/.test(client)
    && withinStringLimit(client) && isRecord(clientErrors)
    && Object.entries(clientErrors).every(([capability, error]) => capabilities.has(capability) && validSnapshotError(error)));
}

function validSnapshotIdentity(value: unknown): value is SafeOdooIdentity {
  return isRecord(value) && Object.keys(value).sort().join(',') === 'client,database,environment,http_port,longpolling_port,release'
    && isNonEmptyString(value.client) && isNonEmptyString(value.release) && value.environment === 'local'
    && withinStringLimit(value.client) && withinStringLimit(value.release) && isNullableString(value.database) && withinStringLimit(value.database)
    && isNullableNumber(value.http_port) && isNullableNumber(value.longpolling_port);
}

function validSnapshotClient(value: unknown): value is OdooClient & { registry_identity: string } {
  return isRecord(value) && Object.keys(value).sort().join(',') === 'environment,local_url,name,registry_identity,release'
    && isNonEmptyString(value.name) && withinStringLimit(value.name) && /^[a-z0-9][a-z0-9_]*$/.test(value.name)
    && !['__proto__', 'constructor', 'prototype'].includes(value.name)
    && isNullableString(value.release) && withinStringLimit(value.release)
    && isNullableString(value.environment) && withinStringLimit(value.environment)
    && isNullableString(value.local_url) && withinStringLimit(value.local_url)
    && isNonEmptyString(value.registry_identity) && withinByteLimit(value.registry_identity, MAX_REGISTRY_IDENTITY_BYTES);
}

function validSnapshotEnvelope(value: unknown): value is OdooSnapshotEnvelope {
  if (!isRecord(value) || value.protocol_version !== 1 || typeof value.process_instance_id !== 'string'
    || !/^[0-9a-f]{32}$/.test(value.process_instance_id) || !isNonEmptyString(value.registry_identity)
    || !withinByteLimit(value.registry_identity, MAX_REGISTRY_IDENTITY_BYTES)
    || !validReleases(value.releases) || (value.releases_error !== null && !validSnapshotError(value.releases_error))
    || !validSnapshotErrors(value.errors)) return false;
  if (Array.isArray(value.clients)) {
    return Object.keys(value).sort().join(',') === 'clients,errors,process_instance_id,protocol_version,registry_epoch,registry_identity,releases,releases_error'
      && typeof value.registry_epoch === 'number' && Number.isSafeInteger(value.registry_epoch) && value.registry_epoch >= 0
      && Object.keys(value.errors).length === 0
      && value.clients.length <= MAX_CLIENTS && value.clients.every(validSnapshotClient)
      && new Set(value.clients.map((client) => client.name)).size === value.clients.length;
  }
  return Object.keys(value).sort().join(',') === 'client,errors,process_instance_id,protocol_version,registry_epoch,registry_identity,releases,releases_error,snapshot'
    && isNonEmptyString(value.client) && withinStringLimit(value.client) && /^[a-z0-9][a-z0-9_]*$/.test(value.client) && typeof value.registry_epoch === 'number'
    && Number.isSafeInteger(value.registry_epoch) && value.registry_epoch >= 0 && isRecord(value.snapshot)
    && Object.keys(value.errors).length <= 1 && (Object.keys(value.errors).length === 0 || Object.prototype.hasOwnProperty.call(value.errors, value.client))
    && Object.keys(value.snapshot).sort().join(',') === 'control,databases,identity,modules,registry_identity,status'
    && isNonEmptyString(value.snapshot.registry_identity) && withinByteLimit(value.snapshot.registry_identity, MAX_REGISTRY_IDENTITY_BYTES)
    && (value.snapshot.identity === null || validSnapshotIdentity(value.snapshot.identity))
    && (value.snapshot.status === null || validStatus(value.snapshot.status))
    && (value.snapshot.control === null || validControl(value.snapshot.control))
    && (value.snapshot.modules === null || validModules(value.snapshot.modules))
    && (value.snapshot.databases === null || validDatabases(value.snapshot.databases));
}

function validLogs(value: unknown): value is OdooLogPage {
  const levels = new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']);
  const entryKeys = 'database,level,logger,message,pid,timestamp';
  const pageKeys = new Set(['entries', 'next_cursor', 'previous_cursor', 'has_more', 'cursor_reset', 'has_previous', 'stale']);
  return isRecord(value) && [...Object.keys(value)].every((key) => pageKeys.has(key)) && Array.isArray(value.entries) && value.entries.length <= MAX_LOG_ENTRIES
    && value.entries.every((entry) => isRecord(entry) && Object.keys(entry).sort().join(',') === entryKeys
      && withinNullableByteLimit(entry.timestamp, MAX_STRING_BYTES) && isNullableNumber(entry.pid)
      && (entry.level === null || levels.has(String(entry.level))) && withinNullableByteLimit(entry.database, MAX_STRING_BYTES)
      && withinNullableByteLimit(entry.logger, MAX_STRING_BYTES) && typeof entry.message === 'string' && withinByteLimit(entry.message, 8192))
    && (typeof value.next_cursor === 'string' ? withinByteLimit(value.next_cursor, MAX_STRING_BYTES) && value.next_cursor.length > 0 : value.next_cursor === null)
    && (value.previous_cursor === undefined || withinNullableByteLimit(value.previous_cursor, MAX_STRING_BYTES))
    && typeof value.cursor_reset === 'boolean'
    && (value.has_previous === undefined || typeof value.has_previous === 'boolean')
    && (value.stale === undefined || typeof value.stale === 'boolean')
    && typeof value.has_more === 'boolean';
}

function sanitizeClients(clients: OdooClient[]): OdooClient[] {
  return clients.map(({ name, release, environment, local_url }) => ({ name, release, environment, local_url }));
}

function sanitizeReleases(releases: { version: string }[]): { version: string }[] {
  return releases.map(({ version }) => ({ version }));
}

function sanitizeStatus(status: OdooStatus): OdooStatus {
  const { state, pid, process_name, pm2_id, startup_mode } = status;
  return { state, pid, process_name, pm2_id, startup_mode };
}

function sanitizeControl(control: OdooControl): OdooControl {
  const { control_mode, lifecycle_eligible, reason } = control;
  return { control_mode, lifecycle_eligible, reason };
}

function sanitizeModules(modules: OdooModule[]): OdooModule[] {
  return modules.map(({ name, display_name, version, installed, installable, update_available, dependencies }) => ({ name, ...(display_name !== undefined ? { display_name } : {}), version, installed, installable, update_available, dependencies: [...dependencies] }));
}

function sanitizeDatabases(databases: OdooDatabase[]): OdooDatabase[] {
  return databases.map(({ name, exists }) => ({ name, exists }));
}

function sanitizeLogs(logs: OdooLogPage): OdooLogPage {
  const { entries, next_cursor, previous_cursor, has_more, cursor_reset, has_previous, stale } = logs;
  return {
    entries: entries.map(({ timestamp, pid, level, database, logger, message }) => ({ timestamp, pid, level, database, logger, message })),
    next_cursor,
    ...(previous_cursor !== undefined ? { previous_cursor } : {}),
    has_more,
    ...(cursor_reset !== undefined ? { cursor_reset } : {}),
    ...(has_previous !== undefined ? { has_previous } : {}),
    ...(stale !== undefined ? { stale } : {}),
  };
}
function retireClient(client: string, readGeneration: Record<string, number>, inFlight: Partial<Record<string, InFlightRead>>, snapshots: Record<string, OdooWorkspaceData>, syncMeta: Record<string, ClientSyncMeta>, cursors: Record<string, string | null>, modes: Record<string, StartMode>, previousStatus: Record<string, RuntimeState | undefined>): void {
  Object.keys(readGeneration).forEach((key) => {
    if (key.startsWith(`${client}:`)) readGeneration[key] += 1;
  });
  Object.keys(inFlight).forEach((key) => {
    if (key.startsWith(`${client}:`)) {
      inFlight[key]?.controller.abort();
      delete inFlight[key];
    }
  });
  delete snapshots[client];
  delete syncMeta[client];
  delete cursors[client];
  delete modes[client];
  delete previousStatus[client];
}

function mergeLogEntries(previous: OdooLogPage['entries'], incoming: OdooLogPage['entries'], duplicatePage: boolean): OdooLogPage['entries'] {
  if (duplicatePage) return previous.slice(-MAX_LOG_ENTRIES);
  return [...previous, ...incoming].slice(-MAX_LOG_ENTRIES);
}

function scheduleBackoff(backoff: { current: PollBackoff }) {
  const currentDelay = backoff.current.delay;
  backoff.current = {
    delay: currentDelay >= 12000 ? 30000 : currentDelay * 2,
    nextAllowed: Date.now() + currentDelay,
  };
}

function selectedDatabase(data: OdooWorkspaceData): string | null {
  const databases = (data.databases ?? []).filter((database) => database.exists);
  return databases.length === 1 ? databases[0].name : null;
}

function safeIdentity(identity: unknown): SafeOdooIdentity | null {
  if (!validIdentity(identity)) return null;
  return {
    client: identity.client,
    release: identity.release,
    environment: identity.environment,
    database: identity.database,
    http_port: identity.http_port,
    longpolling_port: identity.longpolling_port,
  };
}

async function mapWithConcurrency<T, R>(items: T[], limit: number, worker: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  let nextIndex = 0;
  const consume = async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await worker(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => consume()));
  return results;
}

class ReadLimiter {
  private active = 0;
  private readonly queue: Array<{ resolve: (reserved: boolean) => void; reject: (error: Error) => void; signal?: AbortSignal; onAbort?: () => void }> = [];

  constructor(private readonly limit: number) {}

  private promoteNext(): void {
    while (this.queue.length > 0) {
      const waiter = this.queue.shift()!;
      if (waiter.onAbort) waiter.signal?.removeEventListener('abort', waiter.onAbort);
      if (waiter.signal?.aborted) {
        waiter.reject(new Error('The read was aborted.'));
        continue;
      }
      this.active += 1;
      waiter.resolve(true);
      return;
    }
  }

  async run<T>(task: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) throw new Error('The read was aborted.');
    let reserved = false;
    if (this.active >= this.limit) {
      reserved = await new Promise<boolean>((resolve, reject) => {
        const waiter = { resolve, reject, signal, onAbort: undefined as (() => void) | undefined };
        waiter.onAbort = () => {
          const index = this.queue.indexOf(waiter);
          if (index >= 0) this.queue.splice(index, 1);
          reject(new Error('The read was aborted.'));
        };
        signal?.addEventListener('abort', waiter.onAbort, { once: true });
        this.queue.push(waiter);
      });
    }
    if (signal?.aborted) {
      if (reserved) {
        this.active -= 1;
        this.promoteNext();
      }
      throw new Error('The read was aborted.');
    }
    if (!reserved) this.active += 1;
    try {
      return await task();
    } finally {
      this.active -= 1;
      this.promoteNext();
    }
  }
}

export function OdooTuiRoute({ api: suppliedApi }: OdooTuiRouteProps) {
  const defaultApi = useMemo(() => createOdooApi(), []);
  const api = suppliedApi ?? defaultApi;
  const [data, setData] = useState<OdooWorkspaceData>({ clients: [] });
  const [state, setState] = useState<WorkspaceState>('loading');
  const [error, setError] = useState<string>();
  const [operationMessage, setOperationMessage] = useState<string>();
  const [operationPhase, setOperationPhase] = useState<'idle' | 'applying'>('idle');
  const [startMode, setStartMode] = useState<StartMode>('client');
  const modeByClient = useRef<Record<string, StartMode>>({});
  const [clientStatuses, setClientStatuses] = useState<Record<string, RuntimeState | undefined>>({});
  const [selectedModules, setSelectedModules] = useState<Set<string>>(new Set());
  const [allModulesSelected, setAllModulesSelected] = useState(false);
  const selectedRef = useRef<string | undefined>(undefined);
  const mountedRef = useRef(true);
  const requestId = useRef(0);
  const snapshotsRef = useRef<Record<string, OdooWorkspaceData>>({});
  const refreshControllerRef = useRef<AbortController | null>(null);
  const operationControllerRef = useRef<AbortController | null>(null);
  const busyRetryTimerRef = useRef<number | null>(null);
  const logCursorsRef = useRef<Record<string, string | null>>({});
  const readLimiterRef = useRef<ReadLimiter | null>(null);
  const logLimiterRef = useRef<ReadLimiter | null>(null);
  const statusPollRef = useRef(false);
  const databasePollRef = useRef(false);
  const registryPollRef = useRef(false);
  const logPollRef = useRef(false);
  const statusBackoffRef = useRef<PollBackoff>({ delay: 3000, nextAllowed: 0 });
  const databaseBackoffRef = useRef<PollBackoff>({ delay: 3000, nextAllowed: 0 });
  const registryBackoffRef = useRef<PollBackoff>({ delay: 3000, nextAllowed: 0 });
  const registryIdentityRef = useRef<Record<string, string>>({});
  const registryEpochRef = useRef(0);
  const snapshotProcessInstanceRef = useRef<string | null>(null);
  const snapshotRegistryIdentityRef = useRef<string | null>(null);
  const snapshotClientsRef = useRef<OdooClient[]>([]);
  const collectionRequestRef = useRef(0);
  const collectionControllerRef = useRef<AbortController | null>(null);
  const selectedSnapshotProcessRef = useRef<string | null>(null);
  const selectedSnapshotEpochRef = useRef<number | null>(null);
  const selectedSnapshotRegistryRef = useRef<string | null>(null);
  const selectedSnapshotClientRef = useRef<string | null>(null);
  const readGenerationRef = useRef<Record<string, number>>({});
  const previousStatusRef = useRef<Record<string, RuntimeState | undefined>>({});
  const syncMetaRef = useRef<Record<string, ClientSyncMeta>>({});
  const registryStateRef = useRef<'confirmed' | 'stale' | 'unavailable'>('unavailable');
  const releasesObservedAtRef = useRef<number | null>(null);
  const releasesStaleRef = useRef(true);
  const selectedReleasesObservedAtRef = useRef<number | null>(null);
  const selectedReleasesStaleRef = useRef(true);
  const releasesSnapshotRef = useRef<{ version: string }[] | undefined>(undefined);
  const [, setSyncRevision] = useState(0);
  const inFlightReadsRef = useRef<Partial<Record<string, InFlightRead>>>({});
  if (!readLimiterRef.current) readLimiterRef.current = new ReadLimiter(4);
  if (!logLimiterRef.current) logLimiterRef.current = new ReadLimiter(1);

  const beginRead = useCallback((client: string, capability: CapabilityName, signal?: AbortSignal) => {
    const key = `${client}:${capability}`;
    if (inFlightReadsRef.current[key]) return readGenerationRef.current[key] ?? 1;
    const generation = (readGenerationRef.current[key] ?? 0) + 1;
    readGenerationRef.current[key] = generation;
    return generation;
  }, []);

  const readIsCurrent = useCallback((client: string, capability: CapabilityName, generation: number, epoch: number) => (
    registryEpochRef.current === epoch && readGenerationRef.current[`${client}:${capability}`] === generation
  ), []);

  const markCapability = useCallback((client: string, capability: CapabilityName, confirmed: boolean, generation?: number) => {
    const current = syncMetaRef.current[client] ?? { capabilities: {}, mutationInvalidated: false };
    const previous = current.capabilities[capability];
    if (previous && generation != null && generation < previous.generation) return;
    current.capabilities[capability] = {
      observedAt: confirmed ? Date.now() : previous?.observedAt ?? 0,
      stale: !confirmed,
      generation: generation ?? (previous?.generation ?? 0) + 1,
    };
    syncMetaRef.current[client] = current;
    setSyncRevision((revision) => revision + 1);
  }, []);

  const capabilityFresh = useCallback((client: string, capability: CapabilityName) => {
    const meta = syncMetaRef.current[client]?.capabilities[capability];
    return Boolean(meta && !meta.stale && meta.observedAt > 0 && Date.now() - meta.observedAt <= CAPABILITY_TTL[capability]);
  }, []);

  const read = useCallback(<T,>(client: string, capability: CapabilityName, task: (signal: AbortSignal) => Promise<T>, callerSignal?: AbortSignal, lane: 'default' | 'logs' = 'default'): Promise<T> => {
    const key = `${client}:${capability}`;
    let entry = inFlightReadsRef.current[key];
    if (entry?.controller.signal.aborted) return Promise.reject(new Error('The read was aborted.'));
    if (!entry) {
      const controller = new AbortController();
      const limiter = lane === 'logs' ? logLimiterRef.current! : readLimiterRef.current!;
      const promise = limiter.run(() => task(controller.signal), controller.signal).finally(() => {
        if (inFlightReadsRef.current[key]?.promise === promise) delete inFlightReadsRef.current[key];
      });
      void promise.catch(() => undefined);
      entry = { promise, controller, subscribers: 0 };
      inFlightReadsRef.current[key] = entry;
    }
    entry.subscribers += 1;
    return new Promise<T>((resolve, reject) => {
      let done = false;
      let released = false;
      const release = () => {
        if (released) return;
        released = true;
        entry!.subscribers -= 1;
        if (entry!.subscribers === 0 && inFlightReadsRef.current[key]?.promise === entry!.promise) {
          entry!.controller.abort();
        }
      };
      const abort = () => {
        if (done) return;
        done = true;
        release();
        reject(new Error('The read was aborted.'));
      };
      if (callerSignal?.aborted) { abort(); return; }
      callerSignal?.addEventListener('abort', abort, { once: true });
      entry!.promise.then((value) => {
        if (done) return;
        done = true;
        callerSignal?.removeEventListener('abort', abort);
        release();
        resolve(value as T);
      }, (error) => {
        if (done) return;
        done = true;
        callerSignal?.removeEventListener('abort', abort);
        release();
        reject(error);
      });
    });
  }, []);

  const refreshGateRef = useRef<Promise<void>>(Promise.resolve());
  const snapshotGateRef = useRef<Promise<void>>(Promise.resolve());
  const collectionRefreshRef = useRef<(() => Promise<boolean>) | null>(null);
  const selectionVersionRef = useRef(0);
  const performRefresh = useCallback(async (preferredClient?: string, expectedSelectionVersion = selectionVersionRef.current, selectedOnly = false): Promise<boolean> => {
    if (!mountedRef.current || expectedSelectionVersion !== selectionVersionRef.current) return false;
    const run = ++requestId.current;
    const controller = new AbortController();
    refreshControllerRef.current = controller;
    if (preferredClient && snapshotsRef.current[preferredClient]) setState('ready');
    else setState('loading');
    setError(undefined);
    setOperationMessage(undefined);
    try {
      if (api.getSnapshot) {
        const getSnapshot = api.getSnapshot;
        const collection = selectedOnly ? {
          protocol_version: 1 as const,
          process_instance_id: snapshotProcessInstanceRef.current!,
          registry_epoch: registryEpochRef.current,
          registry_identity: snapshotRegistryIdentityRef.current!,
          clients: snapshotClientsRef.current.map((client) => ({ ...client, registry_identity: registryIdentityRef.current[client.name] })),
          releases: releasesSnapshotRef.current ?? [],
          releases_error: selectedReleasesStaleRef.current ? { code: 'RELEASES_UNAVAILABLE' } : null,
          errors: {},
        } : await getSnapshot(undefined, controller.signal);
        if (!mountedRef.current || controller.signal.aborted || run !== requestId.current || !validSnapshotEnvelope(collection) || !Array.isArray(collection.clients)) return false;
        const processChanged = snapshotProcessInstanceRef.current !== null && snapshotProcessInstanceRef.current !== collection.process_instance_id;
        if (processChanged) {
          Object.keys(syncMetaRef.current).forEach((client) => { syncMetaRef.current[client] = { capabilities: {}, mutationInvalidated: true }; });
          releasesObservedAtRef.current = null;
          releasesStaleRef.current = true;
          selectedReleasesObservedAtRef.current = null;
          selectedReleasesStaleRef.current = true;
          setClientStatuses({});
          operationControllerRef.current?.abort();
          setOperationPhase('idle');
          Object.values(snapshotsRef.current).forEach((snapshot) => { delete snapshot.status; });
        }
        if (!processChanged && snapshotProcessInstanceRef.current !== null && collection.registry_epoch < registryEpochRef.current) return false;
        snapshotProcessInstanceRef.current = collection.process_instance_id;
        registryEpochRef.current = collection.registry_epoch;
        snapshotRegistryIdentityRef.current = collection.registry_identity;
        const clients = sanitizeClients(collection.clients);
        snapshotClientsRef.current = clients;
        const nextIdentity: Record<string, string> = {};
        collection.clients.forEach((client) => { nextIdentity[client.name] = client.registry_identity; });
        const previousIdentity = registryIdentityRef.current;
        const selectedBefore = selectedRef.current;
        new Set([...Object.keys(previousIdentity), ...Object.keys(nextIdentity)]).forEach((client) => {
          if (!nextIdentity[client] || nextIdentity[client] !== previousIdentity[client]) {
            retireClient(client, readGenerationRef.current, inFlightReadsRef.current, snapshotsRef.current, syncMetaRef.current, logCursorsRef.current, modeByClient.current, previousStatusRef.current);
          }
        });
        registryIdentityRef.current = nextIdentity;
        setClientStatuses((current) => Object.fromEntries(Object.entries(current).filter(([client]) => Boolean(nextIdentity[client]) && previousIdentity[client] === nextIdentity[client])));
        const releases = collection.releases_error === null ? sanitizeReleases(collection.releases) : undefined;
        if (releases) {
          releasesSnapshotRef.current = releases;
          releasesObservedAtRef.current = Date.now();
          releasesStaleRef.current = false;
        } else {
          releasesStaleRef.current = true;
        }
        const selected = preferredClient && clients.some((client) => client.name === preferredClient)
          ? preferredClient
          : selectedBefore && clients.some((client) => client.name === selectedBefore) ? selectedBefore : clients[0]?.name;
        selectedRef.current = selected;
        clients.forEach((client) => {
          if (!snapshotsRef.current[client.name]) snapshotsRef.current[client.name] = { clients, selectedClient: client.name };
        });
        markCapability('__registry__', 'registry', true, run);
        markCapability('__registry__', 'releases', Boolean(releases), run);
        registryStateRef.current = 'confirmed';
        setData((current) => ({ ...(selected ? snapshotsRef.current[selected] ?? {} : {}), clients, ...(releases ? { releases } : current.releases ? { releases: current.releases } : {}), selectedClient: selected }));
        if (!selected) { setState('ready'); return true; }
        const selectedPayload = await getSnapshot(selected, controller.signal);
        if (!mountedRef.current || controller.signal.aborted || run !== requestId.current) return false;
        if (!validSnapshotEnvelope(selectedPayload) || selectedPayload.client !== selected || !selectedPayload.snapshot) {
          const meta = syncMetaRef.current[selected] ?? { capabilities: {}, mutationInvalidated: false };
          syncMetaRef.current[selected] = { ...meta, capabilities: {}, mutationInvalidated: true };
          selectedSnapshotProcessRef.current = null;
          selectedSnapshotEpochRef.current = null;
          selectedSnapshotRegistryRef.current = null;
          selectedSnapshotClientRef.current = null;
          operationControllerRef.current?.abort();
          setOperationPhase('idle');
          selectedReleasesObservedAtRef.current = null;
          selectedReleasesStaleRef.current = true;
          setClientStatuses((current) => { const next = { ...current }; delete next[selected]; return next; });
          if (snapshotsRef.current[selected]) delete snapshotsRef.current[selected].status;
          setData((current) => { const { status: _status, ...rest } = current; return rest; });
          setSyncRevision((revision) => revision + 1);
          return false;
        }
        const selectedCoherent = selectedPayload.process_instance_id === collection.process_instance_id
          && selectedPayload.registry_epoch === collection.registry_epoch
          && selectedPayload.registry_identity === collection.registry_identity
          && selectedPayload.snapshot.registry_identity === nextIdentity[selected];
        if (!selectedCoherent) {
          if (mountedRef.current) {
            const meta = syncMetaRef.current[selected] ?? { capabilities: {}, mutationInvalidated: false };
            syncMetaRef.current[selected] = { ...meta, capabilities: {}, mutationInvalidated: true };
            selectedSnapshotProcessRef.current = null;
            selectedSnapshotEpochRef.current = null;
            selectedSnapshotRegistryRef.current = null;
            selectedSnapshotClientRef.current = null;
            operationControllerRef.current?.abort();
            setOperationPhase('idle');
            selectedReleasesObservedAtRef.current = null;
            selectedReleasesStaleRef.current = true;
            setClientStatuses({});
            Object.values(snapshotsRef.current).forEach((snapshot) => { delete snapshot.status; });
            setData((current) => { const { status: _status, ...rest } = current; return rest; });
            setSyncRevision((revision) => revision + 1);
            collectionRefreshRef.current?.();
            if (selectedOnly) setError('The Odoo process changed; confirming the current registry.');
          }
          return false;
        }
        selectedSnapshotProcessRef.current = selectedPayload.process_instance_id;
        selectedSnapshotEpochRef.current = selectedPayload.registry_epoch;
        selectedSnapshotRegistryRef.current = selectedPayload.registry_identity;
        selectedSnapshotClientRef.current = selected;
        const selectedSnapshot = selectedPayload.snapshot;
        const selectedErrors = selectedPayload.errors[selected] ?? {};
        const selectedRegistryClient = clients.find((client) => client.name === selected);
        const identityProvenance = selectedSnapshot.identity !== null
          && selectedSnapshot.identity.client === selected
          && selectedSnapshot.identity.release === selectedRegistryClient?.release
          && selectedSnapshot.identity.environment === selectedRegistryClient?.environment
          && selectedSnapshot.registry_identity === registryIdentityRef.current[selected];
        const accepted = (capability: string, value: unknown): boolean => value !== null && selectedErrors[capability] === undefined && identityProvenance;
        const next: OdooWorkspaceData = {
          ...(snapshotsRef.current[selected] ?? {}),
          clients,
          selectedClient: selected,
          ...(selectedPayload.releases_error === null ? { releases: sanitizeReleases(selectedPayload.releases) } : {}),
          ...(accepted('identity', selectedSnapshot.identity) ? { identity: selectedSnapshot.identity! } : {}),
          ...(accepted('status', selectedSnapshot.status) ? { status: selectedSnapshot.status! } : {}),
          ...(accepted('control', selectedSnapshot.control) ? { control: selectedSnapshot.control! } : {}),
          ...(accepted('modules', selectedSnapshot.modules) ? { modules: sanitizeModules(selectedSnapshot.modules!) } : {}),
          ...(accepted('databases', selectedSnapshot.databases) ? { databases: sanitizeDatabases(selectedSnapshot.databases!) } : {}),
        };
        const releasesAccepted = selectedPayload.releases_error === null;
        if (!accepted('status', selectedSnapshot.status)) {
          delete next.status;
          setClientStatuses((current) => { const statuses = { ...current }; delete statuses[selected]; return statuses; });
          if (snapshotsRef.current[selected]) delete snapshotsRef.current[selected].status;
        }
        if (releasesAccepted) {
          releasesSnapshotRef.current = sanitizeReleases(selectedPayload.releases);
          selectedReleasesObservedAtRef.current = Date.now();
          selectedReleasesStaleRef.current = false;
        } else {
          selectedReleasesStaleRef.current = true;
        }
        if (accepted('status', selectedSnapshot.status)) setClientStatuses((current) => ({ ...current, [selected]: selectedSnapshot.status!.state }));
        markCapability('__registry__', 'releases', releasesAccepted, run);
        (['identity', 'status', 'control', 'modules', 'databases'] as const).forEach((capability) => markCapability(selected, capability, accepted(capability, selectedSnapshot[capability]), run));
        snapshotsRef.current[selected] = next;
        setData(next);
        const reportedMode: StartMode = accepted('control', selectedSnapshot.control) && selectedSnapshot.control!.control_mode === 'database_manager' ? 'database_manager' : 'client';
        modeByClient.current[selected] = modeByClient.current[selected] ?? reportedMode;
        setStartMode(modeByClient.current[selected]);
        const incomplete = !releasesAccepted || !accepted('identity', selectedSnapshot.identity) || !accepted('status', selectedSnapshot.status)
          || !accepted('control', selectedSnapshot.control) || !accepted('modules', selectedSnapshot.modules) || !accepted('databases', selectedSnapshot.databases);
        setState('ready');
        setError(incomplete ? 'Some Odoo data could not be confirmed.' : undefined);
        if (!incomplete) {
          const meta = syncMetaRef.current[selected] ?? { capabilities: {}, mutationInvalidated: false };
          syncMetaRef.current[selected] = { ...meta, mutationInvalidated: false };
          setSyncRevision((revision) => revision + 1);
        }
        return !incomplete;
      }
      const registryEpoch = registryEpochRef.current;
      const registryGeneration = beginRead('__registry__', 'registry', controller.signal);
      const clientsPayload = await read('__registry__', 'registry', (signal) => api.listClients(signal), controller.signal);
      if (!mountedRef.current || controller.signal.aborted || run !== requestId.current || !readIsCurrent('__registry__', 'registry', registryGeneration, registryEpoch)) return false;
      if (!validClients(clientsPayload.clients)) throw new Error('The Odoo client registry response is invalid.');
      const clients = sanitizeClients(clientsPayload.clients);
      const nextRegistryIdentity: Record<string, string> = {};
      clients.forEach((client) => { nextRegistryIdentity[client.name] = JSON.stringify([client.name, client.release, client.environment, client.local_url]); });
      const previousRegistryIdentity = registryIdentityRef.current;
      const registryChanged = Object.keys(nextRegistryIdentity).length !== Object.keys(previousRegistryIdentity).length
        || Object.keys(nextRegistryIdentity).some((client) => nextRegistryIdentity[client] !== previousRegistryIdentity[client]);
      const selectedRetiredClient = selectedRef.current;
      const selectedRetired = Boolean(selectedRetiredClient && (!nextRegistryIdentity[selectedRetiredClient] || previousRegistryIdentity[selectedRetiredClient] !== nextRegistryIdentity[selectedRetiredClient]));
      if (registryChanged) registryEpochRef.current += 1;
      new Set([...Object.keys(registryIdentityRef.current), ...Object.keys(nextRegistryIdentity)]).forEach((client) => {
        if (!nextRegistryIdentity[client] || registryIdentityRef.current[client] !== nextRegistryIdentity[client]) {
          retireClient(client, readGenerationRef.current, inFlightReadsRef.current, snapshotsRef.current, syncMetaRef.current, logCursorsRef.current, modeByClient.current, previousStatusRef.current);
        }
      });
      setClientStatuses((current) => Object.fromEntries(Object.entries(current).filter(([client]) => nextRegistryIdentity[client] && registryIdentityRef.current[client] === nextRegistryIdentity[client])));
      registryIdentityRef.current = nextRegistryIdentity;
      clients.forEach((client) => {
        if (!snapshotsRef.current[client.name]) snapshotsRef.current[client.name] = { clients, selectedClient: client.name };
      });
      registryStateRef.current = 'confirmed';
      markCapability('__registry__', 'registry', true, registryGeneration);
      if (selectedRetired) {
        operationControllerRef.current?.abort();
        setOperationPhase('idle');
        setSelectedModules(new Set());
        setAllModulesSelected(false);
        setStartMode('client');
      }
      const selected = preferredClient && clients.some((client) => client.name === preferredClient)
        ? preferredClient
        : selectedRef.current && clients.some((client) => client.name === selectedRef.current)
          ? selectedRef.current
          : clients[0]?.name;
      selectedRef.current = selected;
      if (selectedRetired && selected === selectedRetiredClient) {
        const meta = syncMetaRef.current[selected] ?? { capabilities: {}, mutationInvalidated: false };
        syncMetaRef.current[selected] = { ...meta, mutationInvalidated: true };
      }
      const cached = selected ? snapshotsRef.current[selected] : undefined;
      setData({ ...(cached ?? {}), clients, releases: data.releases ?? releasesSnapshotRef.current ?? cached?.releases, selectedClient: selected });
      if (!selected) { setState('ready'); return true; }
      const query = selected;
      const readEpoch = registryEpochRef.current;
      const queryRegistryIdentity = JSON.stringify([query, clients.find((client) => client.name === query)?.release, clients.find((client) => client.name === query)?.environment, clients.find((client) => client.name === query)?.local_url]);
      const generations = {
        releases: beginRead('__registry__', 'releases', controller.signal),
        identity: beginRead(query, 'identity', controller.signal),
        status: beginRead(query, 'status', controller.signal),
        control: beginRead(query, 'control', controller.signal),
        modules: beginRead(query, 'modules', controller.signal),
        databases: beginRead(query, 'databases', controller.signal),
        logs: beginRead(query, 'logs', controller.signal),
      };
      const results = await Promise.allSettled([
        read('__registry__', 'releases', (signal) => api.listReleases(signal), controller.signal),
        read(query, 'identity', (signal) => api.getIdentity(query, signal), controller.signal),
        read(query, 'status', (signal) => api.getStatus(query, signal), controller.signal),
        read(query, 'control', (signal) => api.getControl(query, signal), controller.signal),
        read(query, 'modules', (signal) => api.getModules(query, signal), controller.signal),
        read(query, 'databases', (signal) => api.getDatabases(query, signal), controller.signal),
        read(query, 'logs', (signal) => api.getLogs(query, { cursor: logCursorsRef.current[query], signal }), controller.signal, 'logs'),
      ]);
      if (!mountedRef.current || controller.signal.aborted || run !== requestId.current || registryEpochRef.current !== readEpoch || registryIdentityRef.current[query] !== queryRegistryIdentity) return false;
      const next: OdooWorkspaceData = { ...(snapshotsRef.current[selected] ?? {}), clients, releases: data.releases ?? snapshotsRef.current[selected]?.releases, selectedClient: selected };
      const [releases, identity, status, control, modules, databases, logs] = results;
      const releasesValid = releases.status === 'fulfilled' && isRecord(releases.value) && validReleases(releases.value.releases);
      const releaseValues = releasesValid ? releases.value.releases : [];
      const identityRaw = identity.status === 'fulfilled' && isRecord(identity.value) ? identity.value.instance : undefined;
      const identityValue = identityRaw && validIdentity(identityRaw) && identityRaw.client === query ? safeIdentity(identityRaw) : null;
      const statusRaw = status.status === 'fulfilled' && isRecord(status.value) ? status.value.status : undefined;
      const controlRaw = control.status === 'fulfilled' && isRecord(control.value) ? control.value.control : undefined;
      const modulesRaw = modules.status === 'fulfilled' && isRecord(modules.value) ? modules.value.modules : undefined;
      const databasesRaw = databases.status === 'fulfilled' && isRecord(databases.value) ? databases.value.databases : undefined;
      const statusValid = validStatus(statusRaw);
      const controlValid = validControl(controlRaw);
      const modulesValid = modules.status === 'fulfilled' && isRecord(modules.value) && modules.value.client === query && validModules(modulesRaw);
      const databasesValid = databases.status === 'fulfilled' && isRecord(databases.value) && databases.value.client === query && validDatabases(databasesRaw);
      const logsValid = logs.status === 'fulfilled' && validLogs(logs.value);
      const releasesAccepted = releasesValid && readIsCurrent('__registry__', 'releases', generations.releases, readEpoch);
      const identityAccepted = Boolean(identityValue) && readIsCurrent(query, 'identity', generations.identity, readEpoch);
      const statusAccepted = statusValid && readIsCurrent(query, 'status', generations.status, readEpoch);
      const controlAccepted = controlValid && readIsCurrent(query, 'control', generations.control, readEpoch);
      const modulesAccepted = modulesValid && readIsCurrent(query, 'modules', generations.modules, readEpoch);
      const databasesAccepted = databasesValid && readIsCurrent(query, 'databases', generations.databases, readEpoch);
      const logsAccepted = logsValid && readIsCurrent(query, 'logs', generations.logs, readEpoch);
      if (releasesAccepted) {
        releasesSnapshotRef.current = sanitizeReleases(releaseValues);
        next.releases = releasesSnapshotRef.current;
      }
      if (identityAccepted) next.identity = identityValue!;
      if (statusAccepted) {
        next.status = sanitizeStatus(statusRaw!);
        setClientStatuses((current) => ({ ...current, [selected]: statusRaw!.state }));
      }
      if (controlAccepted) next.control = sanitizeControl(controlRaw!);
      if (modulesAccepted) next.modules = sanitizeModules(modulesRaw!);
      if (databasesAccepted) next.databases = sanitizeDatabases(databasesRaw!);
      if (logsAccepted) {
        const logValue = sanitizeLogs(logs.value);
        const previousLogs = snapshotsRef.current[query]?.logs;
        const previousCursor = logCursorsRef.current[query] ?? null;
        const entries = logValue.cursor_reset || !previousCursor
          ? logValue.entries.slice(-MAX_LOG_ENTRIES)
          : mergeLogEntries(previousLogs?.entries ?? [], logValue.entries, logValue.next_cursor === previousCursor);
        next.logs = { ...logValue, entries };
        logCursorsRef.current[query] = logValue.next_cursor;
      }
      releasesObservedAtRef.current = releasesAccepted ? Date.now() : releasesObservedAtRef.current;
      releasesStaleRef.current = !releasesAccepted;
      if (releasesAccepted) markCapability('__registry__', 'releases', true, generations.releases);
      markCapability(query, 'identity', identityAccepted, generations.identity);
      markCapability(query, 'status', statusAccepted, generations.status);
      markCapability(query, 'control', controlAccepted, generations.control);
      markCapability(query, 'modules', modulesAccepted, generations.modules);
      markCapability(query, 'databases', databasesAccepted, generations.databases);
      markCapability(query, 'logs', logsAccepted, generations.logs);
      snapshotsRef.current[selected] = next;
      setData(next);
      if (modulesAccepted) {
        const selectableModuleNames = new Set(modulesRaw!.filter((module) => module.installed && module.installable !== false).map((module) => module.name));
        setSelectedModules((current) => new Set([...current].filter((name) => selectableModuleNames.has(name))));
      }
      const reportedMode: StartMode = controlAccepted && controlRaw!.control_mode === 'database_manager' ? 'database_manager' : 'client';
      if (!modeByClient.current[query]) modeByClient.current[query] = reportedMode;
      setStartMode(modeByClient.current[query]);
      const incomplete = !releasesAccepted || !identityAccepted || !statusAccepted || !controlAccepted || !modulesAccepted || !databasesAccepted;
      if (incomplete) {
        setState('ready');
        setError('Some Odoo data could not be confirmed.');
      } else {
        setState('ready');
        const meta = syncMetaRef.current[query];
        if (!incomplete && meta?.mutationInvalidated) {
          syncMetaRef.current[query] = { ...meta, mutationInvalidated: false };
          setSyncRevision((revision) => revision + 1);
        }
      }
      return !incomplete;
    } catch (reason) {
      if (isSnapshotBusy(reason)) {
        const retrySelectionVersion = selectionVersionRef.current;
        if (mountedRef.current) {
          if (busyRetryTimerRef.current != null) window.clearTimeout(busyRetryTimerRef.current);
          busyRetryTimerRef.current = window.setTimeout(() => {
            busyRetryTimerRef.current = null;
            if (mountedRef.current && selectionVersionRef.current === retrySelectionVersion
              && (!preferredClient || selectedRef.current === preferredClient)) void refresh(preferredClient, selectedOnly);
          }, 250);
        }
        return false;
      }
      if (preferredClient && errorCode(reason) === 'INSTANCE_NOT_FOUND'
        && mountedRef.current && !controller.signal.aborted && run === requestId.current
        && expectedSelectionVersion === selectionVersionRef.current && selectedRef.current === preferredClient) {
        delete registryIdentityRef.current[preferredClient];
        snapshotClientsRef.current = snapshotClientsRef.current.filter((entry) => entry.name !== preferredClient);
        retireClient(preferredClient, readGenerationRef.current, inFlightReadsRef.current, snapshotsRef.current, syncMetaRef.current, logCursorsRef.current, modeByClient.current, previousStatusRef.current);
        setClientStatuses((current) => { const next = { ...current }; delete next[preferredClient]; return next; });
        selectedRef.current = undefined;
        selectedSnapshotClientRef.current = null;
        selectedSnapshotProcessRef.current = null;
        selectedSnapshotEpochRef.current = null;
        selectedSnapshotRegistryRef.current = null;
        setData((current) => ({ ...current, clients: current.clients?.filter((entry) => entry.name !== preferredClient), selectedClient: undefined }));
        setSyncRevision((revision) => revision + 1);
        collectionRefreshRef.current?.();
        return false;
      }
      if (!mountedRef.current || controller.signal.aborted || run !== requestId.current) return false;
      if (controller.signal.aborted) return false;
      if (api.getSnapshot) selectedReleasesStaleRef.current = true;
      if (preferredClient) {
        if (api.getSnapshot) {
          const meta = syncMetaRef.current[preferredClient] ?? { capabilities: {}, mutationInvalidated: false };
          syncMetaRef.current[preferredClient] = { ...meta, capabilities: {}, mutationInvalidated: true };
          selectedSnapshotProcessRef.current = null;
          selectedSnapshotEpochRef.current = null;
          selectedSnapshotRegistryRef.current = null;
          selectedSnapshotClientRef.current = null;
          operationControllerRef.current?.abort();
          setOperationPhase('idle');
          selectedReleasesObservedAtRef.current = null;
          selectedReleasesStaleRef.current = true;
          setClientStatuses((current) => { const next = { ...current }; delete next[preferredClient]; return next; });
          if (snapshotsRef.current[preferredClient]) delete snapshotsRef.current[preferredClient].status;
          setData((current) => { const { status: _status, ...rest } = current; return rest; });
          setSyncRevision((revision) => revision + 1);
        }
        setState('ready');
        registryStateRef.current = 'stale';
        setError('The latest Odoo data could not be refreshed.');
        return false;
      } else if (registryStateRef.current === 'confirmed' || Object.keys(registryIdentityRef.current).length > 0 || Object.keys(snapshotsRef.current).length > 0) {
        setState('ready');
        registryStateRef.current = 'stale';
        setError('The latest Odoo data could not be refreshed.');
        return false;
      } else {
        setData({ clients: [] });
        registryStateRef.current = 'unavailable';
        setState('unavailable');
        setError('The Odoo backend is unavailable.');
        return false;
      }
    }
  }, [api]);

  const refresh = useCallback((preferredClient?: string, selectedOnly = false): Promise<boolean> => {
    const expectedSelectionVersion = selectionVersionRef.current;
    const previousSnapshotGate = snapshotGateRef.current;
    const run = () => previousSnapshotGate.then(
      () => performRefresh(preferredClient, expectedSelectionVersion, selectedOnly),
      () => performRefresh(preferredClient, expectedSelectionVersion, selectedOnly),
    );
    const queued = refreshGateRef.current.then(run, run);
    refreshGateRef.current = queued.then(() => undefined, () => undefined);
    snapshotGateRef.current = queued.then(() => undefined, () => undefined);
    return queued;
  }, [performRefresh]);

  const performCollection = useCallback(async (): Promise<boolean> => {
    if (!api.getSnapshot || !mountedRef.current) return false;
    const sequence = ++collectionRequestRef.current;
    collectionControllerRef.current?.abort();
    const controller = new AbortController();
    collectionControllerRef.current = controller;
    const requestedProcess = snapshotProcessInstanceRef.current;
    const requestedEpoch = registryEpochRef.current;
    const requestedSelectionVersion = selectionVersionRef.current;
    try {
      const collection = await api.getSnapshot(undefined, controller.signal);
      if (!mountedRef.current || controller.signal.aborted || sequence !== collectionRequestRef.current || requestedSelectionVersion !== selectionVersionRef.current) return false;
      if (!validSnapshotEnvelope(collection) || !Array.isArray(collection.clients)) {
        registryStateRef.current = 'stale';
        setState((current) => current === 'loading' ? 'unavailable' : current);
        setError('The latest Odoo data could not be refreshed.');
        return false;
      }
      const previousEpoch = registryEpochRef.current;
      const previousSnapshotRegistry = snapshotRegistryIdentityRef.current;
      const processChanged = snapshotProcessInstanceRef.current !== null && snapshotProcessInstanceRef.current !== collection.process_instance_id;
      if (!processChanged && collection.registry_epoch < registryEpochRef.current) return false;
      if ((requestedProcess !== snapshotProcessInstanceRef.current && collection.process_instance_id !== snapshotProcessInstanceRef.current)
        || (requestedProcess === snapshotProcessInstanceRef.current && requestedEpoch !== registryEpochRef.current
          && collection.registry_epoch !== registryEpochRef.current)) return false;
      const registryChanged = processChanged || collection.registry_epoch !== previousEpoch || collection.registry_identity !== previousSnapshotRegistry;
      if (registryChanged) {
        requestId.current += 1;
        refreshControllerRef.current?.abort();
        operationControllerRef.current?.abort();
        setOperationPhase('idle');
      }
      const selectedBefore = selectedRef.current;
      if (registryChanged && selectedBefore) {
        const meta = syncMetaRef.current[selectedBefore] ?? { capabilities: {}, mutationInvalidated: false };
        syncMetaRef.current[selectedBefore] = { ...meta, mutationInvalidated: true, capabilities: {} };
        selectedSnapshotProcessRef.current = null;
        selectedSnapshotEpochRef.current = null;
        selectedSnapshotRegistryRef.current = null;
        selectedSnapshotClientRef.current = null;
        selectedReleasesObservedAtRef.current = null;
        selectedReleasesStaleRef.current = true;
        setClientStatuses({});
        Object.values(snapshotsRef.current).forEach((snapshot) => { delete snapshot.status; });
      }
      if (processChanged) {
        Object.keys(syncMetaRef.current).forEach((client) => { syncMetaRef.current[client] = { capabilities: {}, mutationInvalidated: true }; });
        selectedReleasesObservedAtRef.current = null;
        selectedReleasesStaleRef.current = true;
        setClientStatuses({});
        Object.values(snapshotsRef.current).forEach((snapshot) => { delete snapshot.status; });
      }
      snapshotProcessInstanceRef.current = collection.process_instance_id;
      registryEpochRef.current = collection.registry_epoch;
      snapshotRegistryIdentityRef.current = collection.registry_identity;
      const clients = sanitizeClients(collection.clients);
      snapshotClientsRef.current = clients;
      const nextIdentity: Record<string, string> = {};
      collection.clients.forEach((client) => { nextIdentity[client.name] = client.registry_identity; });
      const previousIdentity = registryIdentityRef.current;
      const selectedChanged = Boolean(selectedBefore && (!nextIdentity[selectedBefore] || nextIdentity[selectedBefore] !== previousIdentity[selectedBefore]));
      new Set([...Object.keys(previousIdentity), ...Object.keys(nextIdentity)]).forEach((client) => {
        if (!nextIdentity[client] || nextIdentity[client] !== previousIdentity[client]) {
          retireClient(client, readGenerationRef.current, inFlightReadsRef.current, snapshotsRef.current, syncMetaRef.current, logCursorsRef.current, modeByClient.current, previousStatusRef.current);
        }
      });
      registryIdentityRef.current = nextIdentity;
      clients.forEach((client) => {
        if (!snapshotsRef.current[client.name]) snapshotsRef.current[client.name] = { clients, selectedClient: client.name };
      });
      const releases = collection.releases_error === null ? sanitizeReleases(collection.releases) : undefined;
      if (releases) {
        releasesSnapshotRef.current = releases;
        releasesObservedAtRef.current = Date.now();
        releasesStaleRef.current = false;
      } else {
        releasesStaleRef.current = true;
      }
      markCapability('__registry__', 'registry', true, collection.registry_epoch);
      markCapability('__registry__', 'releases', Boolean(releases), collection.registry_epoch);
      registryStateRef.current = 'confirmed';
      const selected = selectedBefore && clients.some((client) => client.name === selectedBefore) ? selectedBefore : clients[0]?.name;
      selectedRef.current = selected;
      if (selectedChanged) {
        operationControllerRef.current?.abort();
        setOperationPhase('idle');
        setSelectedModules(new Set());
        setAllModulesSelected(false);
        setStartMode('client');
      }
      setData((current) => ({ ...(selected ? snapshotsRef.current[selected] ?? {} : {}), clients, ...(releases ? { releases } : current.releases ? { releases: current.releases } : {}), selectedClient: selected }));
      if ((selectedChanged || registryChanged) && selected) void refresh(selected, true);
      return true;
    } catch (reason) {
      if (isSnapshotBusy(reason)) return false;
      if (mountedRef.current && !controller.signal.aborted && sequence === collectionRequestRef.current) {
        registryStateRef.current = 'stale';
        setState((current) => current === 'loading' ? 'unavailable' : 'ready');
        setError('The latest Odoo data could not be refreshed.');
      }
      return false;
    }
  }, [api, refresh]);

  const refreshCollection = useCallback((): Promise<boolean> => {
    const queued = snapshotGateRef.current.then(performCollection, performCollection);
    snapshotGateRef.current = queued.then(() => undefined, () => undefined);
    return queued;
  }, [performCollection]);
  collectionRefreshRef.current = refreshCollection;

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!api.getSnapshot || state !== 'ready') return undefined;
    let active = true;
    let timer: number | undefined;
    const schedule = () => {
      if (!active) return;
      timer = window.setTimeout(async () => {
        try { await refreshCollection(); } finally { schedule(); }
      }, 30000);
    };
    schedule();
    return () => { active = false; if (timer != null) window.clearTimeout(timer); };
  }, [api, refreshCollection, state]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      refreshControllerRef.current?.abort();
      collectionControllerRef.current?.abort();
      if (busyRetryTimerRef.current != null) window.clearTimeout(busyRetryTimerRef.current);
      collectionRequestRef.current += 1;
      operationControllerRef.current?.abort();
      Object.values(inFlightReadsRef.current).forEach((entry) => entry?.controller.abort());
      inFlightReadsRef.current = {};
    };
  }, []);

  useEffect(() => {
    if (state !== 'ready' || data.clients.length === 0) return undefined;
    let active = true;
    const controller = new AbortController();
    const poll = async () => {
      if (!active || statusPollRef.current) return;
      if (Date.now() < statusBackoffRef.current.nextAllowed) return;
      statusPollRef.current = true;
      const clients = data.clients;
      const processInstance = snapshotProcessInstanceRef.current;
      try {
        const results = await mapWithConcurrency(clients, 4, async (client) => {
          const epoch = registryEpochRef.current;
          const generation = beginRead(client.name, 'status', controller.signal);
          const expectedIdentity = registryIdentityRef.current[client.name];
          if (!active || !expectedIdentity) return { client: client.name, epoch, generation, processInstance, registryIdentity: expectedIdentity, status: null };
          try {
            const result = await read(client.name, 'status', (signal) => api.getStatus(client.name, signal), controller.signal);
            return { client: client.name, epoch, generation, processInstance, registryIdentity: expectedIdentity, status: validStatus(result.status) ? result.status : null };
          } catch {
            return { client: client.name, epoch, generation, processInstance, registryIdentity: expectedIdentity, status: null };
          }
        });
        if (!active) return;
        const statuses: Record<string, RuntimeState | undefined> = {};
        results.forEach((result) => {
          if (result?.status && result.processInstance === snapshotProcessInstanceRef.current && result.registryIdentity === registryIdentityRef.current[result.client] && readIsCurrent(result.client, 'status', result.generation, result.epoch)) markCapability(result.client, 'status', true, result.generation);
        });
        setData((current) => {
          let next = current;
          results.forEach((result) => {
            if (!result || !result.status || result.processInstance !== snapshotProcessInstanceRef.current || result.registryIdentity !== registryIdentityRef.current[result.client] || !readIsCurrent(result.client, 'status', result.generation, result.epoch)) return;
            if (!current.clients.some((client) => client.name === result.client)) return;
            statuses[result.client] = result.status.state;
            const snapshot = { ...(snapshotsRef.current[result.client] ?? {}), clients: current.clients, selectedClient: result.client, status: sanitizeStatus(result.status) };
            snapshotsRef.current[result.client] = snapshot;
            if (current.selectedClient === result.client) next = snapshot;
          });
          return next;
        });
        clients.forEach((client) => {
          const result = results.find((candidate) => candidate?.client === client.name);
          if (result && !result.status && result.processInstance === snapshotProcessInstanceRef.current && result.registryIdentity === registryIdentityRef.current[result.client] && readIsCurrent(result.client, 'status', result.generation, result.epoch)) markCapability(client.name, 'status', false, result.generation);
        });
        const failed = results.some((result) => !result.status);
        if (failed) scheduleBackoff(statusBackoffRef);
        else statusBackoffRef.current = { delay: 3000, nextAllowed: 0 };
        setClientStatuses((current) => ({ ...current, ...statuses }));
      } finally {
        statusPollRef.current = false;
      }
    };
    let timer: number | undefined;
    const schedule = () => {
      if (!active) return;
      const delay = Math.max(0, (statusBackoffRef.current.nextAllowed || Date.now() + 3000) - Date.now());
      timer = window.setTimeout(async () => { try { await poll(); } finally { schedule(); } }, delay);
    };
    if (data.clients.length > 0 && data.clients.every((client) => capabilityFresh(client.name, 'status'))) schedule();
    else void poll().then(schedule, schedule);
    return () => { active = false; controller.abort(); if (timer != null) window.clearTimeout(timer); };
  }, [api, data.clients, state]);

  useEffect(() => {
    if (Boolean(api.getSnapshot) || state !== 'ready' || data.clients.length === 0) return undefined;
    let active = true;
    const controller = new AbortController();
    const poll = async () => {
      if (!active || databasePollRef.current) return;
      if (Date.now() < databaseBackoffRef.current.nextAllowed) return;
      databasePollRef.current = true;
      const clients = data.clients;
      try {
        const results = await mapWithConcurrency(clients, 4, async (client) => {
          const epoch = registryEpochRef.current;
          const generation = beginRead(client.name, 'databases', controller.signal);
          const expectedIdentity = JSON.stringify([client.name, client.release, client.environment, client.local_url]);
          if (!active || registryIdentityRef.current[client.name] !== expectedIdentity) return { client: client.name, epoch, generation, databases: null };
          try {
            const result = await read(client.name, 'databases', (signal) => api.getDatabases(client.name, signal), controller.signal);
            return { client: client.name, epoch, generation, databases: result.client === client.name && validDatabases(result.databases) ? result.databases : null };
          } catch {
            return { client: client.name, epoch, generation, databases: null };
          }
        });
        if (!active) return;
        results.forEach((result) => {
          if (result?.databases && readIsCurrent(result.client, 'databases', result.generation, result.epoch)) markCapability(result.client, 'databases', true, result.generation);
        });
        setData((current) => {
          let next = current;
          results.forEach((result) => {
            if (!result || !result.databases || !readIsCurrent(result.client, 'databases', result.generation, result.epoch)) return;
            if (!current.clients.some((client) => client.name === result.client)) return;
            const safeDatabases = sanitizeDatabases(result.databases);
            const snapshot = { ...(snapshotsRef.current[result.client] ?? {}), clients: current.clients, selectedClient: current.selectedClient, databases: safeDatabases };
            snapshotsRef.current[result.client] = snapshot;
            if (current.selectedClient === result.client) next = snapshot;
          });
          return next;
        });
        clients.forEach((client) => {
          const result = results.find((candidate) => candidate?.client === client.name);
          if (result && !result.databases && readIsCurrent(result.client, 'databases', result.generation, result.epoch)) markCapability(client.name, 'databases', false, result.generation);
        });
        const failed = results.some((result) => !result.databases);
        if (failed) scheduleBackoff(databaseBackoffRef);
        else databaseBackoffRef.current = { delay: 3000, nextAllowed: 0 };
      } finally {
        databasePollRef.current = false;
      }
    };
    let timer: number | undefined;
    const schedule = () => {
      if (!active) return;
      const delay = Math.max(0, (databaseBackoffRef.current.nextAllowed || Date.now() + 15000) - Date.now());
      timer = window.setTimeout(async () => { try { await poll(); } finally { schedule(); } }, delay);
    };
    if (data.clients.length > 0 && data.clients.every((client) => capabilityFresh(client.name, 'databases'))) schedule();
    else void poll().then(schedule, schedule);
    return () => { active = false; controller.abort(); if (timer != null) window.clearTimeout(timer); };
  }, [api, data.clients, state]);

  useEffect(() => {
    if (Boolean(api.getSnapshot) || state !== 'ready') return undefined;
    let active = true;
    const controller = new AbortController();
    const poll = async () => {
      if (!active || registryPollRef.current) return;
      if (Date.now() < registryBackoffRef.current.nextAllowed) return;
      registryPollRef.current = true;
      try {
        const pollEpoch = registryEpochRef.current;
        const pollGeneration = beginRead('__registry__', 'registry', controller.signal);
        const releaseGeneration = beginRead('__registry__', 'releases', controller.signal);
        const [clientsResult, releasesResult] = await Promise.allSettled([
          read('__registry__', 'registry', (signal) => api.listClients(signal), controller.signal),
          read('__registry__', 'releases', (signal) => api.listReleases(signal), controller.signal),
        ]);
        if (!active || !readIsCurrent('__registry__', 'registry', pollGeneration, pollEpoch)) return;
        const releasePayload = releasesResult.status === 'fulfilled' && isRecord(releasesResult.value) ? releasesResult.value.releases : undefined;
        const releasesCurrent = validReleases(releasePayload)
          && readIsCurrent('__registry__', 'releases', releaseGeneration, pollEpoch);
        const safeReleases = releasesCurrent ? sanitizeReleases(releasePayload) : undefined;
        if (releasesCurrent) {
          releasesObservedAtRef.current = Date.now();
          releasesStaleRef.current = false;
          markCapability('__registry__', 'releases', true, releaseGeneration);
        } else {
          releasesStaleRef.current = true;
          markCapability('__registry__', 'releases', false, releaseGeneration);
          scheduleBackoff(registryBackoffRef);
        }
        if (clientsResult.status !== 'fulfilled') {
          registryStateRef.current = 'stale';
          markCapability('__registry__', 'registry', false, pollGeneration);
          scheduleBackoff(registryBackoffRef);
          if (safeReleases) setData((current) => ({ ...current, releases: safeReleases }));
          setSyncRevision((revision) => revision + 1);
          return;
        }
        const rawClients = isRecord(clientsResult.value) ? clientsResult.value.clients : undefined;
        if (!validClients(rawClients)) {
          registryStateRef.current = 'stale';
          markCapability('__registry__', 'registry', false, pollGeneration);
          scheduleBackoff(registryBackoffRef);
          if (safeReleases) setData((current) => ({ ...current, releases: safeReleases }));
          setSyncRevision((revision) => revision + 1);
          return;
        }
        const clients = sanitizeClients(rawClients);
        markCapability('__registry__', 'registry', true, pollGeneration);
        if (releasesCurrent) registryBackoffRef.current = { delay: 3000, nextAllowed: 0 };
        setSyncRevision((revision) => revision + 1);
        const nextIdentity: Record<string, string> = {};
        clients.forEach((client) => { nextIdentity[client.name] = JSON.stringify([client.name, client.release, client.environment, client.local_url]); });
        const previousIdentity = registryIdentityRef.current;
        const changed = Object.keys(nextIdentity).length !== Object.keys(previousIdentity).length
          || Object.keys(nextIdentity).some((name) => nextIdentity[name] !== previousIdentity[name]);
        const selectedRetiredClient = selectedRef.current;
        const selectedRetired = Boolean(selectedRetiredClient && (!nextIdentity[selectedRetiredClient] || previousIdentity[selectedRetiredClient] !== nextIdentity[selectedRetiredClient]));
        if (changed) registryEpochRef.current += 1;
        registryStateRef.current = 'confirmed';
        new Set([...Object.keys(previousIdentity), ...Object.keys(nextIdentity)]).forEach((client) => {
          if (!nextIdentity[client] || nextIdentity[client] !== previousIdentity[client]) {
            retireClient(client, readGenerationRef.current, inFlightReadsRef.current, snapshotsRef.current, syncMetaRef.current, logCursorsRef.current, modeByClient.current, previousStatusRef.current);
          }
        });
        setClientStatuses((current) => Object.fromEntries(Object.entries(current).filter(([client]) => nextIdentity[client] && registryIdentityRef.current[client] === nextIdentity[client])));
        if (selectedRetired) {
          operationControllerRef.current?.abort();
          setOperationPhase('idle');
          setSelectedModules(new Set());
          setAllModulesSelected(false);
          setStartMode('client');
        }
        registryIdentityRef.current = nextIdentity;
        clients.forEach((client) => {
          if (!snapshotsRef.current[client.name]) snapshotsRef.current[client.name] = { clients, selectedClient: client.name };
        });
        const selected = selectedRef.current && clients.some((client) => client.name === selectedRef.current)
          ? selectedRef.current
          : clients[0]?.name;
        selectedRef.current = selected;
        if (selectedRetired && selected === selectedRetiredClient) {
          const meta = syncMetaRef.current[selected] ?? { capabilities: {}, mutationInvalidated: false };
          syncMetaRef.current[selected] = { ...meta, mutationInvalidated: true };
        }
        if (changed) {
          setData((current) => ({ ...(selected ? snapshotsRef.current[selected] ?? {} : {}), clients, selectedClient: selected, ...(safeReleases ? { releases: safeReleases } : {}) }));
          if (selected) void refresh(selected, true); else setState('ready');
        } else if (releasesCurrent) {
          setData((current) => ({ ...current, clients, releases: safeReleases }));
        }
      } finally {
        registryPollRef.current = false;
      }
    };
    let timer: number | undefined;
    const schedule = () => {
      if (!active) return;
      const delay = Math.max(0, (registryBackoffRef.current.nextAllowed || Date.now() + 30000) - Date.now());
      timer = window.setTimeout(async () => { try { await poll(); } finally { schedule(); } }, delay);
    };
    if (capabilityFresh('__registry__', 'registry') && capabilityFresh('__registry__', 'releases')) schedule();
    else void poll().then(schedule, schedule);
    return () => { active = false; controller.abort(); if (timer != null) window.clearTimeout(timer); };
  }, [api, refresh, state]);

  useEffect(() => {
    const client = data.selectedClient;
    if (state !== 'ready' || !client) return undefined;
    const currentStatus = data.status?.state;
    const previousStatus = previousStatusRef.current[client];
    previousStatusRef.current[client] = currentStatus;
    if (previousStatus && currentStatus && previousStatus !== currentStatus) void refresh(client, true);
    const timer = window.setTimeout(() => {
      if (selectedRef.current === client) void refresh(client, true);
    }, 60000);
    return () => window.clearTimeout(timer);
  }, [api, data.selectedClient, data.status?.state, refresh, state]);

  useEffect(() => {
    const client = data.selectedClient;
    if (state !== 'ready' || !client || data.status?.state !== 'online') return undefined;
    let active = true;
    const controller = new AbortController();
    const poll = async () => {
      if (!active || logPollRef.current) return;
      logPollRef.current = true;
      try {
        const epoch = registryEpochRef.current;
        const generation = beginRead(client, 'logs', controller.signal);
        const cursor = logCursorsRef.current[client] ?? null;
        const logs = await read(client, 'logs', (signal) => api.getLogs(client, { cursor, signal }), controller.signal, 'logs');
        if (active && selectedRef.current === client && validLogs(logs) && readIsCurrent(client, 'logs', generation, epoch)) {
          markCapability(client, 'logs', true, generation);
          const logValue = sanitizeLogs(logs);
          const previous = snapshotsRef.current[client]?.logs;
          const entries = logValue.cursor_reset || !cursor
            ? logValue.entries.slice(-MAX_LOG_ENTRIES)
            : mergeLogEntries(previous?.entries ?? [], logValue.entries, logValue.next_cursor === cursor);
          const mergedLogs = { ...logValue, entries };
          logCursorsRef.current[client] = logs.next_cursor;
          const snapshot = { ...(snapshotsRef.current[client] ?? {}), clients: snapshotsRef.current[client]?.clients ?? [], selectedClient: client, logs: mergedLogs };
          snapshotsRef.current[client] = snapshot;
          setData((current) => current.selectedClient === client ? { ...current, logs: mergedLogs } : current);
        }
      } catch {
        // Log availability is optional and must not hide the confirmed workspace.
        if (active && !controller.signal.aborted) markCapability(client, 'logs', false);
      } finally {
        logPollRef.current = false;
      }
    };
    let timer: number | undefined;
    const schedule = () => {
      if (!active) return;
      timer = window.setTimeout(async () => { try { await poll(); } finally { schedule(); } }, 3000);
    };
    if (capabilityFresh(client, 'logs')) schedule();
    else void poll().then(schedule, schedule);
    return () => { active = false; controller.abort(); if (timer != null) window.clearTimeout(timer); };
  }, [api, data.selectedClient, data.status?.state, state]);

  const onClientChange = useCallback((client: string) => {
    selectionVersionRef.current += 1;
    requestId.current += 1;
    refreshControllerRef.current?.abort();
    if (busyRetryTimerRef.current != null) {
      window.clearTimeout(busyRetryTimerRef.current);
      busyRetryTimerRef.current = null;
    }
    selectedRef.current = client;
    selectedSnapshotProcessRef.current = null;
    selectedSnapshotEpochRef.current = null;
    selectedSnapshotRegistryRef.current = null;
    selectedSnapshotClientRef.current = null;
    operationControllerRef.current?.abort();
    setOperationPhase('idle');
    const meta = syncMetaRef.current[client] ?? { capabilities: {}, mutationInvalidated: false };
    syncMetaRef.current[client] = { ...meta, capabilities: {}, mutationInvalidated: true };
    setSyncRevision((revision) => revision + 1);
    setSelectedModules(new Set());
    setAllModulesSelected(false);
    setStartMode('client');
    modeByClient.current[client] = 'client';
    setData((current) => ({ ...(snapshotsRef.current[client] ?? {}), clients: current.clients, releases: current.releases, selectedClient: client }));
    void refresh(client, true);
  }, [refresh]);

  const selectedClient = data.clients.find((client) => client.name === data.selectedClient);
  const releaseMismatch = Boolean(selectedClient && data.identity && selectedClient.release && data.identity.release !== selectedClient.release);
  const releaseCompatible = Boolean(selectedClient?.release && data.identity?.release && selectedClient.release === data.identity.release
    && selectedClient.environment === data.identity.environment
    && data.releases?.some((release) => release.version === data.identity?.release));
  const clientName = data.selectedClient;
  const releaseObservedAt = api.getSnapshot ? selectedReleasesObservedAtRef.current : releasesObservedAtRef.current;
  const releaseStale = api.getSnapshot ? selectedReleasesStaleRef.current : releasesStaleRef.current;
  const coreReadConfirmed = Boolean(clientName
    && registryStateRef.current === 'confirmed'
    && releaseObservedAt != null
    && !releaseStale
    && Date.now() - releaseObservedAt <= 90000
    && releaseCompatible
    && capabilityFresh(clientName, 'identity')
    && capabilityFresh(clientName, 'status')
    && capabilityFresh(clientName, 'control'));
  const moduleReadConfirmed = Boolean(clientName && capabilityFresh(clientName, 'modules') && capabilityFresh(clientName, 'databases'));
  const eligible = state === 'ready'
    && operationPhase === 'idle'
    && !releaseMismatch
    && data.status?.state !== 'errored'
    && data.control?.control_mode === 'client'
    && data.control.lifecycle_eligible === true
    && data.identity?.client === data.selectedClient
    && (!api.getSnapshot || (selectedSnapshotClientRef.current === clientName
      && selectedSnapshotProcessRef.current === snapshotProcessInstanceRef.current
      && selectedSnapshotEpochRef.current === registryEpochRef.current
      && selectedSnapshotRegistryRef.current === snapshotRegistryIdentityRef.current))
    && Boolean(api.getSnapshot)
    && coreReadConfirmed
    && !Boolean(clientName && syncMetaRef.current[clientName]?.mutationInvalidated);

  const openLifecycle = useCallback((operation: LifecycleOperation, _initiator: HTMLElement) => {
    const client = data.selectedClient;
    if (!client || !eligible) return;
    const operationSelectionVersion = selectionVersionRef.current;
    const operationRegistryIdentity = registryIdentityRef.current[client];
    const operationProcessInstance = snapshotProcessInstanceRef.current;
    const operationEpoch = registryEpochRef.current;
    const operationPrecondition = api.getSnapshot
      ? { registry_identity: operationRegistryIdentity!, process_instance_id: operationProcessInstance!, registry_epoch: operationEpoch }
      : undefined;
    if (api.getSnapshot && (!operationRegistryIdentity || !operationProcessInstance)) return;
    const selectableModuleNames = new Set((data.modules ?? []).filter((module) => module.installed && module.installable !== false).map((module) => module.name));
    const currentSelectedModules = [...selectedModules].filter((name) => selectableModuleNames.has(name));
    const selector = operation === 'restart'
      ? allModulesSelected
        ? { update_all: true as const }
        : currentSelectedModules.length > 0
          ? { modules: currentSelectedModules }
          : undefined
      : undefined;
    if (selector && !moduleReadConfirmed) return;
    const currentMeta = syncMetaRef.current[client] ?? { capabilities: {}, mutationInvalidated: false };
    syncMetaRef.current[client] = { ...currentMeta, mutationInvalidated: true };
    setSyncRevision((revision) => revision + 1);
    const operationController = new AbortController();
    operationControllerRef.current?.abort();
    operationControllerRef.current = operationController;
    setOperationPhase('applying');
    setOperationMessage(selector ? `Restarting ${client} with module update…` : `${operation} in progress…`);
    void (async () => {
      try {
        if (api.getSnapshot && (selectionVersionRef.current !== operationSelectionVersion
          || registryIdentityRef.current[client] !== operationRegistryIdentity
          || snapshotProcessInstanceRef.current !== operationProcessInstance
          || registryEpochRef.current !== operationEpoch)) return;
        if (operation === 'restart') {
          await api.restart(client, `RESTART ${client}`, selector, startMode, operationController.signal, operationPrecondition);
        } else if (operation === 'start') {
          await api.start(client, `START ${client}`, startMode, operationController.signal, operationPrecondition);
        } else {
          await api.stop(client, `STOP ${client}`, operationController.signal, operationPrecondition);
        }
        if (!mountedRef.current || operationController.signal.aborted || selectionVersionRef.current !== operationSelectionVersion
          || (api.getSnapshot && (registryIdentityRef.current[client] !== operationRegistryIdentity
            || snapshotProcessInstanceRef.current !== operationProcessInstance
            || registryEpochRef.current !== operationEpoch))) return;
        if (operation === 'restart' && selector) {
          setSelectedModules(new Set());
          setAllModulesSelected(false);
        }
        setOperationMessage(`${operation} completed; confirming runtime state…`);
        await refresh(client, true);
      } catch (reason) {
        if (operationController.signal.aborted) return;
        setOperationMessage(reason instanceof Error ? reason.message : `The ${operation} operation failed.`);
      } finally {
        if (!operationController.signal.aborted) setOperationPhase('idle');
      }
    })();
  }, [allModulesSelected, api, data.modules, data.selectedClient, eligible, moduleReadConfirmed, refresh, selectedModules, startMode]);

  const workspaceData = { ...data, identity: data.identity, status: data.status, control: data.control };
  return (
    <>
      <OdooWorkspace
        data={workspaceData}
        state={state}
        error={error}
        operationPhase={operationPhase}
        operationMessage={operationMessage}
        readModelConfirmed={coreReadConfirmed}
        moduleReadConfirmed={moduleReadConfirmed}
        startMode={startMode}
        clientStatuses={clientStatuses}
        clientSnapshots={snapshotsRef.current}
        allModulesSelected={allModulesSelected}
        selectedModules={selectedModules}
        onClientChange={onClientChange}
        onClientListOpen={() => undefined}
        onStartModeChange={(mode) => {
          setStartMode(mode);
          if (selectedRef.current) modeByClient.current[selectedRef.current] = mode;
          if (mode === 'database_manager') {
            setSelectedModules(new Set());
            setAllModulesSelected(false);
          }
        }}
        onRefresh={() => void refresh(selectedRef.current, Boolean(snapshotProcessInstanceRef.current))}
        onLifecycle={openLifecycle}
        onAllToggle={(selected) => {
          setAllModulesSelected(selected);
          if (selected) setSelectedModules(new Set());
        }}
        onModuleToggle={(name, selected) => setSelectedModules((current) => {
          const next = new Set(current);
          if (selected) next.add(name); else next.delete(name);
          return next;
        })}
      />
    </>
  );
}

export default OdooTuiRoute;
export { validSnapshotEnvelope };
