import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createOdooApi } from './odooApi';
import { OdooWorkspace, type LifecycleOperation, type WorkspaceState } from './OdooWorkspace';
import type { OdooApi, OdooWorkspaceData, RuntimeState, StartMode } from './types';

export interface OdooTuiRouteProps {
  api?: OdooApi;
}

function selectedDatabase(data: OdooWorkspaceData): string | null {
  const databases = (data.databases ?? []).filter((database) => database.exists);
  return databases.length === 1 ? databases[0].name : null;
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
  const requestId = useRef(0);

  const refresh = useCallback(async (preferredClient?: string) => {
    const run = ++requestId.current;
    setState('loading');
    setError(undefined);
    setOperationMessage(undefined);
    try {
      const clientsPayload = await api.listClients();
      if (run !== requestId.current) return;
      const clients = Array.isArray(clientsPayload.clients) ? clientsPayload.clients : [];
      const selected = preferredClient && clients.some((client) => client.name === preferredClient)
        ? preferredClient
        : selectedRef.current && clients.some((client) => client.name === selectedRef.current)
          ? selectedRef.current
          : clients[0]?.name;
      selectedRef.current = selected;
      setData({ clients, selectedClient: selected });
      if (!selected) { setState('ready'); return; }
      const query = selected;
      const results = await Promise.allSettled([
        api.listReleases(),
        api.getIdentity(query),
        api.getStatus(query),
        api.getControl(query),
        api.getModules(query),
        api.getDatabases(query),
        api.getLogs(query),
      ]);
      if (run !== requestId.current) return;
      const next: OdooWorkspaceData = { clients, selectedClient: selected };
      const [releases, identity, status, control, modules, databases, logs] = results;
      if (releases.status === 'fulfilled') next.releases = releases.value.releases;
      if (identity.status === 'fulfilled') next.identity = identity.value.instance;
      if (status.status === 'fulfilled') {
        next.status = status.value.status;
        setClientStatuses((current) => ({ ...current, [selected]: status.value.status.state }));
      }
      if (control.status === 'fulfilled') next.control = control.value.control;
      if (modules.status === 'fulfilled') next.modules = modules.value.modules;
      if (databases.status === 'fulfilled') next.databases = databases.value.databases;
      if (logs.status === 'fulfilled') next.logs = logs.value;
      setData(next);
      const reportedMode: StartMode = control.status === 'fulfilled' && control.value.control.control_mode === 'database_manager' ? 'database_manager' : 'client';
      if (!modeByClient.current[query]) modeByClient.current[query] = reportedMode;
      setStartMode(modeByClient.current[query]);
      const failed = results.slice(0, 6).find((result) => result.status === 'rejected');
      if (failed) {
        setState('error');
        setError('Some Odoo data could not be confirmed.');
      } else {
        setState('ready');
      }
    } catch {
      if (run !== requestId.current) return;
      setData({ clients: [] });
      setState('unavailable');
      setError('The Odoo backend is unavailable.');
    }
  }, [api]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const client = data.selectedClient;
    if (state !== 'ready' || !client || data.status?.state !== 'online') return undefined;
    let active = true;
    const poll = async () => {
      try {
        const logs = await api.getLogs(client);
        if (active && selectedRef.current === client) {
          setData((current) => current.selectedClient === client ? { ...current, logs } : current);
        }
      } catch {
        // Log availability is optional and must not hide the confirmed workspace.
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [api, data.selectedClient, data.status?.state, state]);

  const refreshClientStatuses = useCallback(async () => {
    const clients = data.clients;
    const results = await Promise.allSettled(clients.map((client) => api.getStatus(client.name)));
    const next: Record<string, RuntimeState | undefined> = {};
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') next[clients[index].name] = result.value.status.state;
    });
    setClientStatuses((current) => ({ ...current, ...next }));
  }, [api, data.clients]);

  const onClientChange = useCallback((client: string) => {
    selectedRef.current = client;
    setSelectedModules(new Set());
    setAllModulesSelected(false);
    setStartMode('client');
    modeByClient.current[client] = 'client';
    setOperationPhase('idle');
    setData((current) => ({ clients: current.clients, selectedClient: client }));
    void refresh(client);
  }, [refresh]);

  const selectedClient = data.clients.find((client) => client.name === data.selectedClient);
  const releaseMismatch = Boolean(selectedClient && data.identity && selectedClient.release && data.identity.release !== selectedClient.release);
  const eligible = state === 'ready'
    && operationPhase === 'idle'
    && !releaseMismatch
    && data.status?.state !== 'errored'
    && data.control?.control_mode === 'client'
    && data.control.lifecycle_eligible === true
    && data.identity?.client === data.selectedClient
    && Boolean(selectedDatabase(data));

  const openLifecycle = useCallback((operation: LifecycleOperation, _initiator: HTMLElement) => {
    const client = data.selectedClient;
    if (!client || !eligible) return;
    const selector = operation === 'restart'
      ? allModulesSelected
        ? { update_all: true as const }
        : selectedModules.size > 0
          ? { modules: [...selectedModules] }
          : undefined
      : undefined;
    setOperationPhase('applying');
    setOperationMessage(selector ? `Restarting ${client} with module update…` : `${operation} in progress…`);
    void (async () => {
      try {
        if (operation === 'restart') {
          await api.restart(client, `RESTART ${client}`, selector, startMode);
        } else if (operation === 'start') {
          await api.start(client, `START ${client}`, startMode);
        } else {
          await api.stop(client, `STOP ${client}`);
        }
        if (operation === 'restart' && selector) {
          setSelectedModules(new Set());
          setAllModulesSelected(false);
        }
        setOperationMessage(`${operation} completed; confirming runtime state…`);
        await refresh(client);
      } catch (reason) {
        setOperationMessage(reason instanceof Error ? reason.message : `The ${operation} operation failed.`);
      } finally {
        setOperationPhase('idle');
      }
    })();
  }, [allModulesSelected, api, data.selectedClient, eligible, refresh, selectedModules, startMode]);

  const workspaceData = { ...data, identity: data.identity, status: data.status, control: data.control };
  return (
    <>
      <OdooWorkspace
        data={workspaceData}
        state={state}
        error={error}
        operationPhase={operationPhase}
        operationMessage={operationMessage}
        startMode={startMode}
        clientStatuses={clientStatuses}
        allModulesSelected={allModulesSelected}
        selectedModules={selectedModules}
        onClientChange={onClientChange}
        onClientListOpen={() => void refreshClientStatuses()}
        onStartModeChange={(mode) => {
          setStartMode(mode);
          if (selectedRef.current) modeByClient.current[selectedRef.current] = mode;
          if (mode === 'database_manager') {
            setSelectedModules(new Set());
            setAllModulesSelected(false);
          }
        }}
        onRefresh={() => void refresh(selectedRef.current)}
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
