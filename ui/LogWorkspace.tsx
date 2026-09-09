import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from './Button';

export type LogLevel = 'ERROR' | 'WARNING' | 'INFO';

export interface OperationalLogEntry {
  id: string;
  timestamp: string;
  level: LogLevel;
  message: string;
  source?: string;
}

export interface OperationalLogPage {
  entries: OperationalLogEntry[];
  next_cursor: string | null;
  previous_cursor?: string | null;
  has_more: boolean;
  has_previous?: boolean;
  stale?: boolean;
}

export type LogPageDirection = 'initial' | 'older' | 'newer';

export interface LogPageRequest {
  client: string;
  cursor: string | null;
  direction: LogPageDirection;
  limit: number;
  signal: AbortSignal;
}

export type LoadLogPage = (request: LogPageRequest) => Promise<OperationalLogPage>;

const PAGE_SIZE = 100;

async function loadLogPage({ client, cursor, direction, limit, signal }: LogPageRequest): Promise<OperationalLogPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  if (direction !== 'initial') params.set('direction', direction);

  const response = await fetch(
    `/api/local/odoo-tui/instance/logs?client=${encodeURIComponent(client)}&${params.toString()}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`Log service unavailable (${response.status})`);
  }

  const payload = (await response.json()) as Partial<OperationalLogPage>;
  if (!Array.isArray(payload.entries) || typeof payload.has_more !== 'boolean') {
    throw new Error('The log service returned an invalid response');
  }

  return {
    entries: payload.entries,
    next_cursor: payload.next_cursor ?? null,
    previous_cursor: payload.previous_cursor ?? null,
    has_more: payload.has_more,
    has_previous: payload.has_previous ?? Boolean(payload.previous_cursor),
    stale: payload.stale ?? false,
  };
}

function formatTimestamp(timestamp: string): string {
  const parsed = Date.parse(timestamp);
  return Number.isNaN(parsed) ? timestamp : new Date(parsed).toLocaleString();
}

function levelClass(level: LogLevel): string {
  if (level === 'ERROR') return 'border-rose-400/30 bg-rose-400/10 text-rose-200';
  if (level === 'WARNING') return 'border-amber-400/30 bg-amber-400/10 text-amber-200';
  return 'border-sky-400/30 bg-sky-400/10 text-sky-200';
}

export interface LogWorkspaceProps {
  client: string | null;
  loadPage?: LoadLogPage;
  pageSize?: number;
}

export function LogWorkspace({ client, loadPage = loadLogPage, pageSize = PAGE_SIZE }: LogWorkspaceProps) {
  const [entries, setEntries] = useState<OperationalLogEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [previousCursor, setPreviousCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [hasPrevious, setHasPrevious] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const loadingRef = useRef(false);
  const entriesRef = useRef<OperationalLogEntry[]>([]);
  const retryRef = useRef<{ cursor: string | null; direction: LogPageDirection } | null>(null);

  const mergeEntries = useCallback((incoming: OperationalLogEntry[], direction: LogPageDirection) => {
    setEntries((current: OperationalLogEntry[]) => {
      const seen = new Set<string>();
      const combined = direction === 'older' ? [...incoming, ...current] : [...current, ...incoming];
      const merged = combined.filter((entry) => {
        if (seen.has(entry.id)) return false;
        seen.add(entry.id);
        return true;
      });
      entriesRef.current = merged;
      return merged;
    });
  }, []);

  const requestPage = useCallback(async (cursor: string | null, direction: LogPageDirection) => {
    if (!client || loadingRef.current) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    retryRef.current = { cursor, direction };
    loadingRef.current = true;
    setLoading(true);
    setError(null);

    try {
      const page = await loadPage({ client, cursor, direction, limit: pageSize, signal: controller.signal });
      if (controller.signal.aborted) return;
      mergeEntries(page.entries, direction);
      setNextCursor(page.next_cursor);
      setPreviousCursor(page.previous_cursor ?? null);
      setHasMore(page.has_more);
      setHasPrevious(page.has_previous ?? Boolean(page.previous_cursor));
      setStale(Boolean(page.stale));
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setError(requestError instanceof Error ? requestError.message : 'Unable to load operational logs');
      setStale(entriesRef.current.length > 0);
    } finally {
      if (!controller.signal.aborted) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [client, loadPage, mergeEntries, pageSize]);

  useEffect(() => {
    abortRef.current?.abort();
    loadingRef.current = false;
    entriesRef.current = [];
    setEntries([]);
    setNextCursor(null);
    setPreviousCursor(null);
    setHasMore(false);
    setHasPrevious(false);
    setError(null);
    setStale(false);
    if (!client) {
      setLoading(false);
    }
    return () => abortRef.current?.abort();
  }, [client]);

  useEffect(() => {
    if (client) void requestPage(null, 'initial');
  }, [client, requestPage]);

  const retry = () => {
    const lastRequest = retryRef.current;
    void requestPage(lastRequest?.cursor ?? null, lastRequest?.direction ?? 'initial');
  };

  const orderedEntries = useMemo(
    () => [...entries].sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp)),
    [entries],
  );

  if (!client) {
    return (
      <section className="rounded-lg border border-border bg-surface-raised p-4" aria-labelledby="operational-log-title">
        <h2 id="operational-log-title" className="text-sm font-semibold text-text">Operational logs</h2>
        <p className="mt-2 text-sm text-text-muted">Select an Odoo client to view its logs.</p>
      </section>
    );
  }

  return (
    <section className="min-w-0 rounded-lg border border-border bg-surface-raised" aria-labelledby="operational-log-title">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4">
        <div>
          <h2 id="operational-log-title" className="text-sm font-semibold text-text">Operational logs</h2>
          <p className="mt-1 text-xs text-text-muted">Bounded cursor history for {client}</p>
        </div>
        {stale ? <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-xs text-amber-200">Stale data</span> : null}
      </div>

      {error ? (
        <div className="m-4 rounded-md border border-rose-400/30 bg-rose-400/10 p-3" role="alert">
          <p className="text-sm text-rose-200">{error}</p>
          <div className="mt-3"><Button variant="danger" disabled={loading} onClick={retry}>Retry</Button></div>
        </div>
      ) : null}

      {loading && entries.length === 0 ? (
        <div className="p-8 text-center text-sm text-text-muted" role="status" aria-live="polite" aria-busy="true">Loading operational logs…</div>
      ) : entries.length === 0 ? (
        <div className="p-8 text-center text-sm text-text-muted">No operational log entries.</div>
      ) : (
        <>
          <div className="max-h-[32rem] divide-y divide-border overflow-y-auto" aria-live="polite">
            {orderedEntries.map((entry: OperationalLogEntry) => (
              <article key={entry.id} className="min-w-0 p-3 sm:p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-text-subtle">
                  <time dateTime={entry.timestamp}>{formatTimestamp(entry.timestamp)}</time>
                  <span className={`rounded-full border px-2 py-0.5 font-medium ${levelClass(entry.level)}`}>{entry.level}</span>
                  {entry.source ? <span className="truncate">{entry.source}</span> : null}
                </div>
                <p className="mt-2 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-text">{entry.message}</p>
              </article>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border p-3">
            <Button variant="secondary" disabled={loading || !hasPrevious || !previousCursor} onClick={() => void requestPage(previousCursor, 'older')}>
              {loading ? 'Loading…' : 'Newer entries'}
            </Button>
            <span className="text-xs text-text-muted">{entries.length} entries loaded</span>
            <Button variant="secondary" disabled={loading || !hasMore || !nextCursor} onClick={() => void requestPage(nextCursor, 'newer')}>
              {loading ? 'Loading…' : 'Older entries'}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
