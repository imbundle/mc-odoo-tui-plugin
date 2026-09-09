import { useEffect, useState } from 'react';
import { Button } from './Button';

export type PasswordCapabilityState =
  | 'loading'
  | 'available'
  | 'unavailable'
  | 'unauthenticated'
  | 'error'
  | 'stale';

export interface PasswordCapability {
  state: PasswordCapabilityState;
  hasPassword: boolean | null;
  message?: string;
  refreshedAt?: string;
}

export interface PasswordCapabilityProps {
  capability: PasswordCapability;
  onRetry?: () => void;
  onReveal: () => Promise<string>;
}

/**
 * The returned password is deliberately kept in component memory only. It is
 * never passed to the generic status UI, logged, or persisted by this component.
 */
export function PasswordCapabilityPanel({
  capability,
  onRetry,
  onReveal,
}: PasswordCapabilityProps) {
  const [revealedPassword, setRevealedPassword] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [revealError, setRevealError] = useState<string | null>(null);

  useEffect(() => () => setRevealedPassword(null), []);

  useEffect(() => {
    setRevealedPassword(null);
    setRevealError(null);
  }, [capability.state, capability.hasPassword]);

  // The read model intentionally exposes presence only when the backend has
  // a separate presence capability. The reveal endpoint remains the authority
  // when presence is unknown.
  const canReveal = capability.state === 'available' && capability.hasPassword !== false;

  async function revealPassword() {
    if (!canReveal || revealing) return;
    setRevealing(true);
    setRevealError(null);
    try {
      const password = await onReveal();
      if (typeof password !== 'string') {
        throw new Error('Invalid password response');
      }
      setRevealedPassword(password);
    } catch {
      setRevealError('The password could not be retrieved. Retry the request.');
      setRevealedPassword(null);
    } finally {
      setRevealing(false);
    }
  }

  function hidePassword() {
    setRevealedPassword(null);
    setRevealError(null);
  }

  const stateLabel: Record<PasswordCapabilityState, string> = {
    loading: 'Checking capability…',
    available: capability.hasPassword === true ? 'Configured' : capability.hasPassword === false ? 'Not configured' : 'Available',
    unavailable: 'Unavailable',
    unauthenticated: 'Authentication required',
    error: 'Unable to check',
    stale: 'Data may be stale',
  };

  const isRecoverable = capability.state === 'error' || capability.state === 'stale';

  return (
    <section
      aria-labelledby="admin-password-heading"
      className="rounded-lg border border-border bg-surface-raised p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium uppercase tracking-wide text-text-muted">Sensitive capability</p>
          <h2 id="admin-password-heading" className="text-lg font-semibold text-text">
            Administrator password
          </h2>
        </div>
        <span
          className="rounded-full border border-white/10 px-2.5 py-1 text-xs font-medium text-text-muted"
          aria-live="polite"
        >
          {stateLabel[capability.state]}
        </span>
      </div>

      <div className="mt-3 space-y-3 text-sm text-text-muted">
        {capability.state === 'loading' && <p role="status">Reading the confirmed password capability…</p>}
        {capability.state === 'available' && capability.hasPassword === false && (
          <p>No administrator password is configured for this instance.</p>
        )}
        {capability.state === 'available' && capability.hasPassword !== false && (
          <p>The capability is available. Retrieval requires an explicit authenticated request.</p>
        )}
        {capability.state === 'unauthenticated' && (
          <p role="alert">Sign in with an authorized Mission Control session to use this capability.</p>
        )}
        {capability.state === 'unavailable' && (
          <p role="alert">This backend does not expose password retrieval for the selected instance.</p>
        )}
        {(capability.state === 'error' || capability.state === 'stale') && (
          <p role="alert">{capability.message || 'The capability status could not be confirmed.'}</p>
        )}
        {capability.refreshedAt && capability.state !== 'loading' && (
          <p className="text-xs">Last confirmed: {capability.refreshedAt}</p>
        )}

        {revealedPassword !== null && (
          <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3">
            <label htmlFor="odoo-admin-password" className="block text-xs font-medium text-amber-100">
              Retrieved password (clear value)
            </label>
            <input
              id="odoo-admin-password"
              type="text"
              readOnly
              value={revealedPassword}
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-white/10 bg-black/20 px-2 py-2 font-mono text-sm text-text"
            />
            <p className="mt-2 text-xs text-amber-100/80">This value is held only until you hide it or leave this view.</p>
          </div>
        )}
        {revealError && <p role="alert" className="text-rose-200">{revealError}</p>}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" disabled={!canReveal || revealing} onClick={revealPassword}>
          {revealing ? 'Retrieving…' : 'Reveal password'}
        </Button>
        {revealedPassword !== null && (
          <Button variant="secondary" onClick={hidePassword}>Hide password</Button>
        )}
        {(isRecoverable || capability.state === 'unavailable' || capability.state === 'unauthenticated') && onRetry && (
          <Button variant="ghost" onClick={onRetry}>Retry status</Button>
        )}
      </div>
    </section>
  );
}

export interface OperationalControlProps {
  confirmedState: 'online' | 'stopped' | 'unknown' | 'unavailable';
  busy?: boolean;
  authenticated?: boolean;
  onStart?: () => Promise<void>;
  onStop?: () => Promise<void>;
  onRestart?: () => Promise<void>;
  error?: string;
}

/** Controls are enabled only from confirmed backend state; actions do not optimistically change it. */
export function GuardedOperationalControls({
  confirmedState,
  busy = false,
  authenticated = false,
  onStart,
  onStop,
  onRestart,
  error,
}: OperationalControlProps) {
  const [pendingAction, setPendingAction] = useState<'start' | 'stop' | 'restart' | null>(null);
  const enabled = authenticated && confirmedState !== 'unknown' && confirmedState !== 'unavailable' && !busy;
  const controlsEnabled = enabled && pendingAction === null;

  async function runAction(action: 'start' | 'stop' | 'restart', operation?: () => Promise<void>) {
    if (!controlsEnabled || !operation) return;
    setPendingAction(action);
    try {
      await operation();
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <section aria-labelledby="operational-controls-heading" className="rounded-lg border border-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium uppercase tracking-wide text-text-muted">Guarded operations</p>
          <h2 id="operational-controls-heading" className="text-lg font-semibold text-text">Instance lifecycle</h2>
        </div>
        <span className="text-sm text-text-muted">Confirmed state: {confirmedState}</span>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-rose-200">{error}</p>}
      {pendingAction && <p role="status" className="mt-3 text-sm text-text-muted">Waiting for backend confirmation of {pendingAction}…</p>}
      {!authenticated && <p className="mt-3 text-sm text-text-muted">Authentication is required before operations can be enabled.</p>}
      {(confirmedState === 'unknown' || confirmedState === 'unavailable') && (
        <p className="mt-3 text-sm text-text-muted">Controls remain disabled until the backend confirms the instance state.</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button disabled={!controlsEnabled || confirmedState === 'online' || !onStart} onClick={() => void runAction('start', onStart)}>Start</Button>
        <Button disabled={!controlsEnabled || confirmedState === 'stopped' || !onStop} onClick={() => void runAction('stop', onStop)}>Stop</Button>
        <Button variant="danger" disabled={!controlsEnabled || !onRestart} onClick={() => void runAction('restart', onRestart)}>Restart</Button>
      </div>
    </section>
  );
}
