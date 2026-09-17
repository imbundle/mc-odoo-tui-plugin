import React from 'react'
import {
  Activity,
  AlertTriangle,
  Database as DatabaseIcon,
  GitBranch,
  ListTree,
  Package,
  Play,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Settings2,
  Square,
} from 'lucide-react'
import { Button } from './Button'
import type {
  OdooClient,
  OdooControl,
  OdooDatabase,
  SafeOdooIdentity,
  OdooModule,
  OdooStatus,
  OdooWorkspaceData,
  RuntimeState,
  StartMode,
} from './types'

export type WorkspaceState = 'loading' | 'ready' | 'error' | 'unavailable'
export type LifecycleOperation = 'start' | 'stop' | 'restart'

export interface OdooWorkspaceProps {
  data: OdooWorkspaceData
  state: WorkspaceState
  error?: string
  operationPhase?: 'idle' | 'planning' | 'applying' | 'pending'
  operationMessage?: string
  readModelConfirmed?: boolean
  moduleReadConfirmed?: boolean
  startMode: StartMode
  clientStatuses: Record<string, RuntimeState | undefined>
  clientSnapshots?: Record<string, OdooWorkspaceData>
  allModulesSelected: boolean
  selectedModules: Set<string>
  onClientChange: (client: string) => void
  onClientListOpen: () => void
  onStartModeChange: (mode: StartMode) => void
  onRefresh: () => void
  onLifecycle: (operation: LifecycleOperation, target: HTMLElement) => void
  onAllToggle: (selected: boolean) => void
  onModuleToggle: (name: string, selected: boolean) => void
}

function stateLabel(state?: RuntimeState): string {
  if (state === 'online') return 'Online'
  if (state === 'stopped') return 'Stopped'
  if (state === 'errored') return 'Errored'
  return 'Unknown'
}

function statusDotClass(state?: RuntimeState): string {
  if (state === 'online') return 'bg-positive shadow-[0_0_0_3px_rgba(52,211,153,0.14)] animate-pulse'
  if (state === 'errored') return 'bg-negative shadow-[0_0_0_3px_rgba(248,113,113,0.14)]'
  if (state === 'stopped') return 'bg-text-muted'
  return 'bg-warning animate-pulse'
}

function formatLogTimestamp(timestamp: string | null): string {
  if (!timestamp) return 'timestamp unavailable'
  const match = timestamp.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)/)
  if (!match) return timestamp
  return `${match[1]} ${match[2].replace('.', ',')}`
}

function logLevelClass(level: string | null): string {
  if (level === 'ERROR' || level === 'CRITICAL') return '!bg-rose-500/15 !text-rose-300 !border !border-rose-400/35'
  if (level === 'WARNING') return '!bg-amber-500/15 !text-amber-300 !border !border-amber-400/35'
  return '!bg-sky-500/15 !text-sky-300 !border !border-sky-400/35'
}

function Panel({
  testId,
  title,
  icon,
  children,
  className = '',
  showHeading = true,
}: {
  testId: string
  title: React.ReactNode
  icon?: React.ReactNode
  children: React.ReactNode
  className?: string
  showHeading?: boolean
}) {
  return (
    <section
      data-testid={testId}
      aria-labelledby={showHeading ? `${testId}-heading` : undefined}
      aria-label={!showHeading ? (typeof title === 'string' ? title : testId) : undefined}
      className={`card border-border bg-surface-raised box-border max-w-full min-w-0 overflow-visible rounded-[var(--control-radius)] border p-3 sm:p-4 ${className}`}
    >
      {showHeading ? (
        <h2 id={`${testId}-heading`} className="text-text flex items-center gap-1.5 text-sm font-semibold">
          {icon}
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  )
}

function MissionControlToggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean
  disabled: boolean
  label: string
  onChange: (checked: boolean) => void
}) {
  return (
    <label
      className="relative inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-end select-none"
      title={label}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="bg-border peer-checked:bg-accent peer-focus-visible:ring-accent/60 relative block h-5 w-9 rounded-full transition-colors duration-200 peer-focus-visible:ring-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-40 after:absolute after:inset-y-0 after:left-0.5 after:my-auto after:h-3.5 after:w-3.5 after:rounded-full after:bg-white after:transition-transform after:duration-200 peer-checked:after:translate-x-[calc(100%+2px)]" />
    </label>
  )
}

function StateNotice({ state, error, onRefresh }: Pick<OdooWorkspaceProps, 'state' | 'error' | 'onRefresh'>) {
  const message =
    state === 'loading'
      ? 'Loading Odoo data…'
      : error || (state === 'unavailable' ? 'The Odoo backend is unavailable.' : 'Some Odoo data could not be loaded.')
  return (
    <div
      data-testid="odoo-tui-state-notice"
      role={state === 'error' || state === 'unavailable' ? 'alert' : 'status'}
      aria-live="polite"
      aria-busy={state === 'loading'}
      className="card border-border bg-surface text-text flex min-h-11 items-center justify-between gap-3 rounded-[var(--control-radius)] border px-4 py-3 text-sm"
    >
      <span className="min-w-0">{message}</span>
      {state !== 'ready' ? (
        <Button icon={<RefreshCw size={15} />} onClick={onRefresh}>
          Refresh
        </Button>
      ) : null}
    </div>
  )
}

export function OdooWorkspace({
  data,
  state,
  error,
  operationPhase = 'idle',
  operationMessage,
  readModelConfirmed = true,
  moduleReadConfirmed = true,
  startMode,
  clientStatuses,
  clientSnapshots,
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
  const logViewportRef = React.useRef<HTMLDivElement>(null)
  const selectedClientButtonRef = React.useRef<HTMLButtonElement>(null)
  const accordionTransitionInitializedRef = React.useRef(false)
  const followLogsRef = React.useRef(true)
  const [clientAccordionOpen, setClientAccordionOpen] = React.useState(false)
  const [accordionInteractive, setAccordionInteractive] = React.useState(false)
  const [modulesInteractive, setModulesInteractive] = React.useState(true)
  const [showJumpToLatest, setShowJumpToLatest] = React.useState(false)
  const latestLog = data.logs?.entries[data.logs.entries.length - 1]
  const latestLogKey = latestLog
    ? `${latestLog.timestamp ?? ''}|${latestLog.level ?? ''}|${latestLog.message}`
    : 'empty'

  React.useEffect(() => {
    if (!accordionTransitionInitializedRef.current) {
      accordionTransitionInitializedRef.current = true
      return
    }
    setAccordionInteractive(false)
    setModulesInteractive(false)
    const timer = window.setTimeout(() => {
      if (clientAccordionOpen) setAccordionInteractive(true)
      else setModulesInteractive(true)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [clientAccordionOpen])

  React.useEffect(() => {
    const viewport = logViewportRef.current
    if (!viewport || !followLogsRef.current) return
    viewport.scrollTop = viewport.scrollHeight
    setShowJumpToLatest(false)
  }, [latestLogKey])

  const handleLogScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const viewport = event.currentTarget
    const atBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 24
    followLogsRef.current = atBottom
    setShowJumpToLatest(!atBottom)
  }

  const jumpToLatest = () => {
    const viewport = logViewportRef.current
    if (!viewport) return
    followLogsRef.current = true
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' })
    setShowJumpToLatest(false)
  }

  const selectedClient: OdooClient | undefined = data.clients.find((client) => client.name === data.selectedClient)
  const identity: SafeOdooIdentity | undefined = data.identity
  const status: OdooStatus | undefined = data.status
  const control: OdooControl | undefined = data.control
  const modules: OdooModule[] = data.modules ?? []
  const selectableModuleNames = new Set(
    modules.filter((module) => module.installed && module.installable !== false).map((module) => module.name),
  )
  const selectedSelectableModules = new Set([...selectedModules].filter((name) => selectableModuleNames.has(name)))
  const databases: OdooDatabase[] = data.databases ?? []
  const confirmedDatabase = databases.filter((database) => database.exists)
  const releaseMismatch = Boolean(
    selectedClient && identity && selectedClient.release && identity.release !== selectedClient.release,
  )
  const busy = operationPhase !== 'idle'
  const eligible =
    readModelConfirmed &&
    state === 'ready' &&
    !busy &&
    !releaseMismatch &&
    control?.control_mode === 'client' &&
    control.lifecycle_eligible === true
  const online = status?.state === 'online'
  const stopped = status?.state === 'stopped'

  return (
    <main
      data-testid="odoo-tui-route"
      className="text-text box-border flex h-full min-h-0 w-full max-w-full min-w-0 flex-1 flex-col gap-4 overflow-x-hidden overflow-y-auto overscroll-contain p-0 sm:gap-5 xl:overflow-y-hidden"
    >
      {state !== 'ready' ? <StateNotice state={state} error={error} onRefresh={onRefresh} /> : null}
      {state === 'ready' && (!readModelConfirmed || !moduleReadConfirmed) ? (
        <p data-testid="odoo-tui-stale-indicator" role="status" aria-live="polite" className="text-text-muted text-xs">
          Some Odoo data is being refreshed; affected actions remain disabled until confirmed.
        </p>
      ) : null}

      <div
        data-testid="desktop-workspace-grid"
        className="grid w-full min-w-0 gap-4 xl:h-full xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(16rem,0.2fr)_minmax(0,0.8fr)] xl:grid-rows-[minmax(0,1fr)] xl:items-stretch xl:gap-4"
      >
        <div data-testid="desktop-left-column" className="min-w-0 space-y-4 xl:flex xl:h-full xl:min-h-0 xl:flex-col">
          <Panel
            testId="client-selection"
            title="Instance"
            showHeading={false}
            className="space-y-4 xl:flex xl:min-h-0 xl:flex-1 xl:flex-col"
          >
            {data.clients.length === 0 ? (
              <p className="text-text-muted mt-2 text-sm">No registered Odoo clients are available.</p>
            ) : (
              <>
                <button
                  ref={selectedClientButtonRef}
                  type="button"
                  aria-label={`Open ${selectedClient?.name || 'selected'} client panel: ${stateLabel(status?.state)}, Odoo ${identity?.release || selectedClient?.release || '?'}, database ${confirmedDatabase[0]?.name || identity?.database || 'Database not confirmed'}, process ${status?.process_name || 'Process identity unavailable'}${status?.pid != null ? `, PID ${status.pid}` : ''}${status?.pm2_id != null ? `, PM2 ${status.pm2_id}` : ''}`}
                  aria-expanded={clientAccordionOpen}
                  aria-controls="client-accordion-list"
                  onClick={() => {
                    setClientAccordionOpen((open) => {
                      const next = !open
                      if (next) onClientListOpen()
                      return next
                    })
                  }}
                  disabled={state === 'loading' || busy}
                  className="group border-border-subtle bg-surface hover:border-accent focus-visible:ring-accent/60 flex min-h-[96px] w-full min-w-0 flex-col rounded-lg border px-3 py-2.5 text-left transition-all duration-200 focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden="true"
                      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${statusDotClass(status?.state)}`}
                    />
                    <span className="text-text min-w-0 flex-1 truncate text-sm font-semibold tracking-[0.04em] uppercase">
                      {selectedClient?.name || 'Select a client'}
                    </span>
                    <span className="bg-accent-subtle text-accent shrink-0 rounded-full px-2.5 py-1 text-xs font-medium">
                      Odoo {identity?.release || selectedClient?.release || '?'}
                    </span>
                  </span>
                  <span
                    data-testid="database-summary"
                    className="text-text-muted mt-2 flex min-w-0 items-center gap-2 text-xs"
                  >
                    <DatabaseIcon aria-hidden="true" size={14} className="shrink-0" />
                    <span className="min-w-0 truncate font-mono">
                      {confirmedDatabase[0]?.name || identity?.database || 'Database not confirmed'}
                    </span>
                  </span>
                  <span
                    data-testid="runtime-status"
                    aria-label="Runtime process"
                    className="text-text-muted mt-1 min-w-0 truncate font-mono text-[11px]"
                  >
                    {status?.process_name || 'Process identity unavailable'}
                    {status?.pid != null ? ` · PID ${status.pid}` : ''}
                    {status?.pm2_id != null ? ` · PM2 ${status.pm2_id}` : ''}
                  </span>
                </button>
                <div
                  id="client-accordion-list"
                  data-testid="client-accordion"
                  role="list"
                  aria-label="Registered Odoo clients"
                  inert={!clientAccordionOpen || !accordionInteractive}
                  className={`overflow-x-hidden overflow-y-auto transition-[max-height,opacity] duration-300 ease-in-out ${clientAccordionOpen ? 'mt-2 max-h-[min(70dvh,2000px)] opacity-100' : 'pointer-events-none max-h-0 opacity-0'}`}
                  aria-hidden={!clientAccordionOpen || !accordionInteractive}
                >
                  <div className="flex flex-col gap-2">
                    {data.clients
                      .filter((client) => client.name !== data.selectedClient)
                      .map((client) => {
                        const clientData = clientSnapshots?.[client.name]
                        const clientState = clientData?.status?.state ?? clientStatuses[client.name]
                        const clientDatabase =
                          clientData?.databases?.find((database) => database.exists)?.name ||
                          clientData?.identity?.database
                        const clientStatus = clientData?.status
                        return (
                          <div key={client.name} role="listitem" className="min-w-0">
                            <button
                              type="button"
                              aria-label={`Select ${client.name} client: ${stateLabel(clientState)}, Odoo ${clientData?.identity?.release || client.release || '?'}, database ${clientDatabase || 'Database not confirmed'}, process ${clientStatus?.process_name || 'unavailable'}${clientStatus?.pid != null ? `, PID ${clientStatus.pid}` : ''}${clientStatus?.pm2_id != null ? `, PM2 ${clientStatus.pm2_id}` : ''}`}
                              onClick={() => {
                                onClientChange(client.name)
                                setClientAccordionOpen(false)
                                selectedClientButtonRef.current?.focus()
                              }}
                              disabled={state === 'loading' || busy}
                              className="group border-border-subtle bg-surface hover:border-accent focus-visible:ring-accent/60 flex min-h-[96px] w-full min-w-0 flex-col rounded-lg border px-3 py-2.5 text-left transition-all duration-200 focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <span className="flex min-w-0 items-center gap-2">
                                <span
                                  aria-hidden="true"
                                  className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${statusDotClass(clientState)}`}
                                />
                                <span className="text-text min-w-0 flex-1 truncate text-sm font-semibold tracking-[0.04em] uppercase">
                                  {client.name}
                                </span>
                                <span className="bg-accent-subtle text-accent shrink-0 rounded-full px-2.5 py-1 text-xs font-medium">
                                  Odoo {clientData?.identity?.release || client.release || '?'}
                                </span>
                              </span>
                              <span className="text-text-muted mt-2 flex min-w-0 items-center gap-2 text-xs">
                                <DatabaseIcon aria-hidden="true" size={14} className="shrink-0" />
                                <span className="min-w-0 truncate font-mono">
                                  {clientDatabase || 'Database not confirmed'}
                                </span>
                              </span>
                              <span className="text-text-muted mt-1 min-w-0 truncate font-mono text-[11px]">
                                {clientStatus?.process_name || stateLabel(clientState)}
                                {clientStatus?.pid != null ? ` · PID ${clientStatus.pid}` : ''}
                                {clientStatus?.pm2_id != null ? ` · PM2 ${clientStatus.pm2_id}` : ''}
                              </span>
                            </button>
                          </div>
                        )
                      })}
                  </div>
                </div>
              </>
            )}
            <div
              data-testid="module-updates"
              aria-label="Modules"
              inert={!modulesInteractive}
              aria-hidden={!modulesInteractive}
              className={`min-w-0 transition-[max-height,opacity] duration-300 ease-in-out xl:flex xl:min-h-0 xl:flex-1 xl:flex-col ${clientAccordionOpen ? 'max-h-0 overflow-hidden opacity-0' : 'border-border-subtle max-h-[4000px] border-t pt-4 opacity-100'}`}
            >
              <div
                id="odoo-modules-content"
                data-testid="module-content"
                aria-hidden={!modulesInteractive}
                className={`min-h-0 transition-[max-height,opacity] duration-300 ease-in-out xl:flex xl:min-h-0 xl:flex-1 xl:flex-col ${modulesInteractive ? 'max-h-none overflow-visible opacity-100' : 'max-h-0 overflow-hidden opacity-0'}`}
              >
                <h3 className="text-text mt-1 flex items-center gap-1.5 text-sm font-semibold">Modules</h3>
                {startMode !== 'client' ? (
                  <div
                    data-testid="module-mode-notice"
                    role="status"
                    aria-live="polite"
                    className="mt-2 inline-flex max-w-full items-start gap-1.5 rounded-full border px-2.5 py-1 text-xs"
                    style={{
                      color: 'var(--color-warning)',
                      borderColor: 'var(--color-warning)',
                      backgroundColor: 'color-mix(in srgb, var(--color-warning) 10%, transparent)',
                    }}
                  >
                    <AlertTriangle aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
                    <span>Module updates are available only in Client mode.</span>
                  </div>
                ) : null}
                <div
                  data-testid="module-list"
                  className="mt-3 flex min-h-0 flex-col gap-1.5 overflow-y-auto pr-1 xl:flex-1"
                >
                  <div
                    className={`relative box-border flex min-h-[48px] w-full max-w-full min-w-0 shrink-0 items-center gap-2 rounded-lg border px-3 py-2 transition-colors ${allModulesSelected ? 'border-border-subtle bg-accent-subtle' : 'border-border-subtle bg-surface'}`}
                  >
                    <span className="text-accent min-w-0 truncate text-sm font-semibold">ALL</span>
                    <span className="text-text-muted min-w-0 flex-1 truncate text-xs">All installed modules</span>
                    <MissionControlToggle
                      checked={allModulesSelected}
                      disabled={!eligible || !moduleReadConfirmed || busy || startMode !== 'client'}
                      label="Select all installed modules"
                      onChange={onAllToggle}
                    />
                  </div>
                  {modules.length === 0 ? (
                    <p className="text-text-muted py-1 text-sm">No client module catalogue is available.</p>
                  ) : (
                    modules.map((module) => {
                      const selectable = module.installed && module.installable !== false
                      const selected = selectedSelectableModules.has(module.name)
                      return (
                        <article
                          key={module.name}
                          className={`relative box-border flex min-h-[76px] w-full max-w-full min-w-0 shrink-0 items-center overflow-hidden rounded-lg border px-3 py-2 transition-colors ${!selectable ? 'border-border-subtle bg-surface cursor-not-allowed opacity-60' : selected ? 'border-border-subtle bg-accent-subtle' : 'border-border-subtle bg-surface hover:bg-surface-raised'}`}
                        >
                          <span className="min-w-0 flex-1 pr-16">
                            <span className="text-text block truncate text-sm font-semibold">
                              {module.display_name || module.name}
                            </span>
                            <span className="text-accent mt-0.5 block truncate font-mono text-xs">{module.name}</span>
                          </span>
                          <span className="border-border-subtle bg-surface-sunken text-text-muted absolute top-2 right-3 rounded-full border px-1.5 py-0.5 font-mono text-[10px] tabular-nums">
                            {module.version || '—'}
                          </span>
                          <MissionControlToggle
                            checked={selected}
                            disabled={
                              !moduleReadConfirmed ||
                              startMode !== 'client' ||
                              allModulesSelected ||
                              !selectable ||
                              busy
                            }
                            label={`Select module ${module.name}`}
                            onChange={(checked) => onModuleToggle(module.name, checked)}
                          />
                        </article>
                      )
                    })
                  )}
                </div>
                {!eligible || !moduleReadConfirmed ? (
                  <p className="text-text-muted mt-2 text-xs">
                    Module updates require a confirmed Client runtime and database.
                  </p>
                ) : null}
              </div>
            </div>
          </Panel>
        </div>

        <Panel
          testId="odoo-logs"
          title="Log"
          showHeading={false}
          className="w-full min-w-0 xl:flex xl:h-full xl:min-h-0 xl:flex-col"
        >
          <div className="mt-2 flex min-w-0 flex-wrap items-start justify-between gap-3 pb-3">
            <div data-testid="lifecycle-actions" className="min-w-0">
              <h3 className="text-text-muted flex items-center gap-1.5 text-xs font-semibold tracking-[0.12em] uppercase">
                <Activity aria-hidden="true" size={14} />
                Lifecycle
              </h3>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  icon={<Play size={15} />}
                  variant="positive"
                  disabled={!eligible || !stopped}
                  onClick={(event) => onLifecycle('start', event.currentTarget)}
                >
                  Start
                </Button>
                <Button
                  icon={<Square size={15} />}
                  variant="danger"
                  disabled={!eligible || !online}
                  onClick={(event) => onLifecycle('stop', event.currentTarget)}
                >
                  Stop
                </Button>
                <Button
                  icon={<RotateCcw size={15} />}
                  variant="primary"
                  disabled={
                    !eligible || !online || (!moduleReadConfirmed && (allModulesSelected || selectedModules.size > 0))
                  }
                  onClick={(event) => onLifecycle('restart', event.currentTarget)}
                >
                  Restart
                </Button>
              </div>
            </div>
            <div className="min-w-0">
              <h3 className="text-text-muted flex items-center gap-1.5 text-xs font-semibold tracking-[0.12em] uppercase">
                <Settings2 aria-hidden="true" size={14} />
                Mode
              </h3>
              <div
                role="group"
                aria-label="Start mode"
                className="mt-2 flex max-w-full min-w-0 items-center gap-1.5 overflow-x-auto"
              >
                {(['client', 'database_manager'] as StartMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={startMode === mode}
                    disabled={busy || state !== 'ready' || online}
                    onClick={() => onStartModeChange(mode)}
                    className={`pill pill-button shrink-0 whitespace-nowrap ${startMode === mode ? 'nav-link-active' : 'pill-subtle'} disabled:pointer-events-none disabled:opacity-50`}
                  >
                    {mode === 'client' ? (
                      <ListTree aria-hidden="true" className="h-3.5 w-3.5" />
                    ) : (
                      <GitBranch aria-hidden="true" className="h-3.5 w-3.5" />
                    )}{' '}
                    {mode === 'client' ? 'Client' : 'Database Manager'}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {!eligible ? (
            <p className="text-text-muted mt-2 text-xs">Actions require a confirmed runtime and database.</p>
          ) : null}
          {!data.logs || data.status?.state !== 'online' || data.logs.entries.length === 0 ? (
            <div
              data-testid="odoo-log-empty-state"
              className="flex min-h-[14rem] flex-1 flex-col items-center justify-center px-6 text-center"
            >
              <span className="border-border-subtle bg-surface-sunken text-text-muted flex h-10 w-10 items-center justify-center rounded-full border">
                <ScrollText aria-hidden="true" size={18} />
              </span>
              <h3 className="text-text mt-3 text-sm font-medium">No logs available</h3>
              <p className="text-text-muted mt-1 max-w-xs text-xs leading-relaxed">
                Logs will appear here when this client reports activity.
              </p>
            </div>
          ) : (
            <>
              {showJumpToLatest ? (
                <div className="mt-3 flex justify-end">
                  <Button onClick={jumpToLatest} ariaLabel="Jump to latest Odoo log entry">
                    Jump to latest
                  </Button>
                </div>
              ) : null}
              <div
                ref={logViewportRef}
                data-testid="odoo-log-viewport"
                aria-label="Odoo log entries"
                onScroll={handleLogScroll}
                className="mt-3 max-h-[min(28rem,calc(100dvh-12rem))] min-h-[10rem] min-w-0 overflow-y-auto overscroll-contain pr-1 xl:max-h-none xl:min-h-0 xl:flex-1"
              >
                <div className="grid gap-2">
                  {data.logs.entries.map((entry, index) => (
                    <article
                      key={`${entry.timestamp ?? 'unknown'}-${entry.pid ?? 'nopid'}-${index}`}
                      className="border-border-subtle box-border max-w-full min-w-0 overflow-hidden rounded-lg border p-3"
                    >
                      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                        <span
                          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${logLevelClass(entry.level)}`}
                        >
                          {entry.level ?? 'INFO'}
                        </span>
                        <time
                          className="border-border bg-surface text-text-muted inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 font-mono text-xs tabular-nums"
                          dateTime={entry.timestamp ?? undefined}
                        >
                          {formatLogTimestamp(entry.timestamp)}
                        </time>
                      </div>
                      <p className="text-text mt-2 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
                        {entry.message}
                      </p>
                    </article>
                  ))}
                </div>
              </div>
            </>
          )}
        </Panel>
      </div>

      {operationMessage || busy ? (
        <section
          data-testid="operation-result"
          aria-live="polite"
          role={operationMessage ? 'status' : undefined}
          className="border-border bg-surface text-text min-h-[44px] rounded-xl border px-4 py-3 text-sm"
        >
          {operationMessage || 'Operation in progress…'}
        </section>
      ) : null}
    </main>
  )
}
