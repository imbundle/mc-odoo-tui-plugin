import React from 'react';
import { Activity, ChevronDown, Play, RefreshCw, RotateCcw, Square } from 'lucide-react';
import { Button } from './Button';
import type { OdooClient, OdooControl, OdooDatabase, OdooIdentity, OdooModule, OdooStatus, OdooWorkspaceData, RuntimeState, StartMode } from './types';

export type WorkspaceState = 'loading' | 'ready' | 'error' | 'unavailable';
export type LifecycleOperation = 'start' | 'stop' | 'restart';

export interface OdooWorkspaceProps {
  data: OdooWorkspaceData;
  state: WorkspaceState;
  error?: string;
  operationPhase?: 'idle' | 'planning' | 'applying' | 'pending';
  operationMessage?: string;
  startMode: StartMode;
  clientStatuses: Record<string, RuntimeState | undefined>;
  allModulesSelected: boolean;
  selectedModules: Set<string>;
  onClientChange: (client: string) => void;
  onClientListOpen: () => void;
  onStartModeChange: (mode: StartMode) => void;
  onRefresh: () => void;
  onLifecycle: (operation: LifecycleOperation, target: HTMLElement) => void;
  onAllToggle: (selected: boolean) => void;
  onModuleToggle: (name: string, selected: boolean) => void;
}

function stateLabel(state?: RuntimeState): string {
  if (state === 'online') return 'Online';
  if (state === 'stopped') return 'Stopped';
  if (state === 'errored') return 'Errored';
  return 'Unknown';
}

function statusDotClass(state?: RuntimeState): string {
  if (state === 'online') return 'bg-positive shadow-[0_0_0_3px_rgba(52,211,153,0.14)] animate-pulse';
  if (state === 'errored') return 'bg-negative shadow-[0_0_0_3px_rgba(248,113,113,0.14)]';
  if (state === 'stopped') return 'bg-text-muted';
  return 'bg-warning animate-pulse';
}
function Panel({ testId, title, children, className = '' }: { testId: string; title: string; children: React.ReactNode; className?: string }) {
  return (
    <section data-testid={testId} aria-labelledby={`${testId}-heading`} className={`card box-border min-w-0 max-w-full overflow-visible rounded-[var(--control-radius)] border border-border bg-surface-raised p-3 sm:p-4 ${className}`}>
      <h2 id={`${testId}-heading`} className="text-sm font-semibold text-text">{title}</h2>
      {children}
    </section>
  );
}

function StateNotice({ state, error, onRefresh }: Pick<OdooWorkspaceProps, 'state' | 'error' | 'onRefresh'>) {
  const message = state === 'loading' ? 'Loading Odoo data…' : error || (state === 'unavailable' ? 'The Odoo backend is unavailable.' : 'Some Odoo data could not be loaded.');
  return (
    <div data-testid="odoo-tui-state-notice" role={state === 'error' || state === 'unavailable' ? 'alert' : 'status'} aria-live="polite" aria-busy={state === 'loading'} className="card flex min-h-11 items-center justify-between gap-3 rounded-[var(--control-radius)] border border-border bg-surface px-4 py-3 text-sm text-text">
      <span className="min-w-0">{message}</span>
      {state !== 'ready' ? <Button icon={<RefreshCw size={15} />} onClick={onRefresh}>Refresh</Button> : null}
    </div>
  );
}

export function OdooWorkspace({
  data,
  state,
  error,
  operationPhase = 'idle',
  operationMessage,
  startMode,
  clientStatuses,
  allModulesSelected,
  selectedModules,
  onClientChange,
  onClientListOpen,
  onStartModeChange,
  onRefresh,
  onLifecycle,
  onAllToggle,
  onModuleToggle,
}: OdooWorkspaceProps) {
  const logViewportRef = React.useRef<HTMLDivElement>(null);
  const followLogsRef = React.useRef(true);
  const [clientListOpen, setClientListOpen] = React.useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = React.useState(false);
  const latestLog = data.logs?.entries[data.logs.entries.length - 1];
  const latestLogKey = latestLog ? `${latestLog.timestamp ?? ''}|${latestLog.level ?? ''}|${latestLog.message}` : 'empty';

  React.useEffect(() => {
    const viewport = logViewportRef.current;
    if (!viewport || !followLogsRef.current) return;
    viewport.scrollTop = viewport.scrollHeight;
    setShowJumpToLatest(false);
  }, [latestLogKey]);

  const handleLogScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const viewport = event.currentTarget;
    const atBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 24;
    followLogsRef.current = atBottom;
    setShowJumpToLatest(!atBottom);
  };

  const jumpToLatest = () => {
    const viewport = logViewportRef.current;
    if (!viewport) return;
    followLogsRef.current = true;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' });
    setShowJumpToLatest(false);
  };

  const selectedClient: OdooClient | undefined = data.clients.find((client) => client.name === data.selectedClient);
  const identity: OdooIdentity | undefined = data.identity;
  const status: OdooStatus | undefined = data.status;
  const control: OdooControl | undefined = data.control;
  const modules: OdooModule[] = data.modules ?? [];
  const databases: OdooDatabase[] = data.databases ?? [];
  const confirmedDatabase = databases.filter((database) => database.exists);
  const releaseMismatch = Boolean(selectedClient && identity && selectedClient.release && identity.release !== selectedClient.release);
  const busy = operationPhase !== 'idle';
  const eligible = state === 'ready' && !busy && !releaseMismatch && control?.control_mode === 'client' && control.lifecycle_eligible === true;
  const online = status?.state === 'online';
  const stopped = status?.state === 'stopped';

  return (
    <main data-testid="odoo-tui-route" className="box-border flex h-full min-h-0 min-w-0 w-full max-w-full flex-1 flex-col gap-4 overflow-x-hidden overflow-y-auto overscroll-contain p-3 text-text sm:gap-5 sm:p-5 xl:overflow-y-hidden">
      {state !== 'ready' ? <StateNotice state={state} error={error} onRefresh={onRefresh} /> : null}

      <Panel testId="client-selection" title="Instance">
        {data.clients.length === 0 ? <p className="mt-2 text-sm text-text-muted">No registered Odoo clients are available.</p> : (
          <>
            <div className="flex flex-wrap items-end gap-2">
              <div className="relative min-w-[12rem] flex-1">
                <span className="block text-xs font-medium text-text">Client</span>
                <button id="odoo-client-selector" type="button" aria-label="Registered Odoo client" aria-expanded={clientListOpen} aria-controls="odoo-client-list" onClick={() => { const next = !clientListOpen; setClientListOpen(next); if (next) onClientListOpen(); }} disabled={state === 'loading' || busy} className="mt-1 flex h-11 min-h-11 w-full min-w-0 items-center justify-between gap-2 rounded-[var(--control-radius)] border border-border bg-surface px-2.5 text-left text-sm text-text transition-colors hover:border-border-subtle hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-60"><span className="min-w-0 truncate">{selectedClient?.name || 'Select a client'}</span><ChevronDown aria-hidden="true" size={16} className="shrink-0 text-text-muted" /></button>
                {clientListOpen ? <div id="odoo-client-list" role="listbox" aria-label="Registered Odoo clients" className="absolute left-0 top-full z-30 mt-2 max-h-72 w-full min-w-[16rem] overflow-y-auto rounded-lg border border-border bg-surface-raised p-1 shadow-xl">
                  {data.clients.map((client) => { const clientState = clientStatuses[client.name] ?? (client.name === data.selectedClient ? status?.state : undefined); return <button key={client.name} type="button" role="option" aria-selected={client.name === data.selectedClient} onClick={() => { onClientChange(client.name); setClientListOpen(false); }} className={`flex min-h-[44px] w-full min-w-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-surface ${client.name === data.selectedClient ? 'bg-accent-subtle' : ''}`}><span aria-hidden="true" className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${statusDotClass(clientState)}`} /><span className="min-w-0 flex-1 truncate text-sm text-text">{client.name}</span><span className="shrink-0 text-xs text-text-muted">{stateLabel(clientState)}</span></button>; })}
                </div> : null}
              </div>
              <div role="group" aria-label="Start mode" className="inline-flex h-11 min-h-11 w-fit max-w-full flex-none items-center gap-1 overflow-hidden rounded-full bg-surface-sunken p-1">
                {(['client', 'database_manager'] as StartMode[]).map((mode) => <button key={mode} type="button" aria-pressed={startMode === mode} disabled={busy || state !== 'ready' || online} onClick={() => onStartModeChange(mode)} className={`h-9 min-h-[36px] min-w-0 flex-none whitespace-nowrap rounded-full px-3 text-xs font-medium leading-none transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${startMode === mode ? 'bg-accent text-white shadow-sm' : 'text-text-muted hover:bg-surface-raised/70 hover:text-text'} disabled:cursor-not-allowed disabled:opacity-50`}>{mode === 'client' ? 'Client' : 'Database Manager'}</button>)}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border-subtle pt-2 text-xs">
              <span><span className="text-text-muted">Odoo</span> <strong className="font-medium text-text">{identity?.release || selectedClient.release || '?'}</strong></span>
              <span data-testid="database-summary"><span className="text-text-muted">DB</span> <strong className="font-mono font-medium text-text">{confirmedDatabase[0]?.name || identity?.database || 'not confirmed'}</strong></span>
            </div>
            <div data-testid="runtime-status" className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border-subtle pt-2 text-xs" aria-label="Runtime status">
              <span data-testid="runtime-status-dot" role="img" aria-label={stateLabel(status?.state)} title={stateLabel(status?.state)} className={`inline-block h-3 w-3 shrink-0 rounded-full border border-border-subtle ${statusDotClass(status?.state)}`} />
              <span className="text-text-muted">{status?.process_name || 'Process identity unavailable'}</span>
              {status?.pid != null ? <span className="text-text-muted">PID {status.pid}</span> : null}
              {status?.pm2_id != null ? <span className="text-text-muted">PM2 {status.pm2_id}</span> : null}
            </div>
            {control?.reason ? <p className="mt-2 text-xs text-text-muted">{control.reason}</p> : null}
            {releaseMismatch ? <p role="alert" className="mt-2 text-xs text-negative">Release mismatch. Actions disabled.</p> : null}
          </>
        )}
        <div data-testid="lifecycle-actions" className="mt-3 border-t border-border-subtle pt-3">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-text-muted"><Activity aria-hidden="true" size={14} />Lifecycle</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button icon={<Play size={15} />} variant="positive" disabled={!eligible || !stopped} onClick={(event) => onLifecycle('start', event.currentTarget)}>Start</Button>
            <Button icon={<Square size={15} />} variant="danger" disabled={!eligible || !online} onClick={(event) => onLifecycle('stop', event.currentTarget)}>Stop</Button>
            <Button icon={<RotateCcw size={15} />} variant="primary" disabled={!eligible || !online} onClick={(event) => onLifecycle('restart', event.currentTarget)}>Restart</Button>
          </div>
          {!eligible ? <p className="mt-2 text-xs text-text-muted">Actions require a confirmed runtime and database.</p> : null}
        </div>
      </Panel>

      <div data-testid="desktop-workspace-grid" className="grid min-w-0 w-full gap-4 xl:flex-1 xl:h-full xl:min-h-0 xl:grid-cols-[minmax(16rem,0.25fr)_minmax(0,0.75fr)] xl:grid-rows-[minmax(0,1fr)] xl:items-stretch">
        <div data-testid="desktop-left-column" className="min-w-0 space-y-4 xl:flex xl:h-full xl:min-h-0 xl:flex-col">
          <Panel testId="module-updates" title="Module updates" className="xl:flex xl:min-h-0 xl:flex-1 xl:flex-col">
            <div data-testid="module-list" className="mt-2 grid min-h-0 gap-1 xl:flex-1 xl:overflow-y-auto xl:pr-1">
              <label title="Update all installed modules" className={`box-border flex min-h-[44px] w-full min-w-0 max-w-full items-center gap-2 rounded-md border px-2.5 py-1 text-sm ${allModulesSelected ? 'border-transparent bg-surface' : 'border-border-subtle bg-surface'}`}><span className="flex min-w-0 items-center gap-2 font-semibold text-accent"><input type="checkbox" aria-label="Select all installed modules" checked={allModulesSelected} disabled={!eligible || busy || startMode !== 'client'} onChange={(event) => onAllToggle(event.target.checked)} className="h-5 w-5 shrink-0 accent-accent" /><span>ALL</span></span></label>
              {modules.length === 0 ? <p className="py-1 text-sm text-text-muted">No client module catalogue is available.</p> : modules.map((module) => <label key={module.name} className={`box-border flex min-h-[44px] w-full min-w-0 max-w-full items-center justify-between gap-2 overflow-hidden rounded-md border px-2.5 py-1 text-sm transition-colors ${module.installed ? 'border-transparent bg-surface' : 'border-border-subtle bg-surface opacity-60 cursor-not-allowed'}`}><span className={`flex min-w-0 flex-1 items-center gap-2 overflow-hidden ${module.installed ? 'text-positive' : 'text-text-muted'}`}><input type="checkbox" aria-label={`Select module ${module.name}`} checked={selectedModules.has(module.name)} disabled={startMode !== 'client' || allModulesSelected || !module.installed || module.installable === false || busy} onChange={(event) => onModuleToggle(module.name, event.target.checked)} className="h-5 w-5 shrink-0 accent-accent" /><span className={`min-w-0 flex-1 truncate ${module.installed ? 'font-semibold' : 'font-medium'}`}>{module.name}</span></span><span className={`max-w-[40%] shrink text-right text-xs [overflow-wrap:anywhere] ${module.installed ? 'text-positive/80' : 'text-text-muted'}`}>{module.version || 'Version unknown'}</span></label>)}
            </div>
            {startMode !== 'client' ? <p className="mt-2 text-xs text-text-muted">Module updates are available only in Client mode.</p> : null}
            {!eligible ? <p className="mt-2 text-xs text-text-muted">Module updates require a confirmed Client runtime and database.</p> : null}
          </Panel>
        </div>

        <Panel testId="odoo-logs" title="Odoo logs" className="min-w-0 w-full xl:flex xl:h-full xl:min-h-0 xl:flex-col">
          {!data.logs ? <p className="mt-3 text-sm text-text-muted">Odoo log is unavailable for this client.</p> : data.status?.state !== 'online' ? <p className="mt-3 text-sm text-text-muted">No Odoo log: this client is not running.</p> : data.logs.entries.length === 0 ? <p className="mt-3 text-sm text-text-muted">No Odoo log entries in the current window.</p> : <>
            {showJumpToLatest ? <div className="mt-3 flex justify-end"><Button onClick={jumpToLatest} ariaLabel="Jump to latest Odoo log entry">Jump to latest</Button></div> : null}
            <div ref={logViewportRef} data-testid="odoo-log-viewport" aria-label="Odoo log entries" onScroll={handleLogScroll} className="mt-3 min-h-[10rem] max-h-[min(28rem,calc(100dvh-12rem))] xl:max-h-none xl:flex-1 xl:min-h-0 min-w-0 overflow-y-auto overscroll-contain pr-1"><div className="grid gap-2">{data.logs.entries.map((entry, index) => <article key={`${entry.timestamp ?? 'unknown'}-${entry.pid ?? 'nopid'}-${index}`} className="box-border min-w-0 max-w-full rounded-lg border border-border-subtle p-3 overflow-hidden"><div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">{entry.timestamp ? <time className="max-w-full break-all" dateTime={entry.timestamp}>{entry.timestamp}</time> : <span>timestamp unavailable</span>}<span className={`rounded-full border px-2 py-1 ${entry.level === 'ERROR' || entry.level === 'CRITICAL' ? 'border-negative text-negative' : entry.level === 'WARNING' ? 'border-warning text-warning' : 'border-accent text-accent'}`}>{entry.level ?? 'INFO'}</span>{entry.pid != null ? <span>PID {entry.pid}</span> : null}{entry.database ? <span className="font-mono">{entry.database}</span> : null}{entry.logger ? <span className="font-mono">{entry.logger}</span> : null}</div><p className="mt-2 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-text">{entry.message}</p></article>)}</div></div>
          </>}
        </Panel>
      </div>

      {(operationMessage || busy) ? <section data-testid="operation-result" aria-live="polite" role={operationMessage ? 'status' : undefined} className="min-h-[44px] rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text">{operationMessage || 'Operation in progress…'}</section> : null}
    </main>
  );
}
