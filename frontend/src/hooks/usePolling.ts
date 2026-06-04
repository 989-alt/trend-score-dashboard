import { useCallback, useEffect, useRef, useState } from "react";

export interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean; // true only on the very first load
  refreshing: boolean; // true while any fetch is in flight
  lastUpdated: Date | null;
  refresh: () => void;
}

export interface PollingOptions {
  /** Poll interval in milliseconds. */
  intervalMs?: number;
  /** Pause polling (e.g. inactive tab). */
  enabled?: boolean;
}

/**
 * Fetches `fetcher` immediately and then every `intervalMs`. Aborts in-flight
 * requests on dependency change / unmount. Keeps the last good `data` when a
 * later fetch errors (so the UI does not flash empty on a transient backend
 * blip). `refresh()` triggers an out-of-band fetch and resets the interval.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown>,
  options: PollingOptions = {},
): PollingState<T> {
  const { intervalMs = 30_000, enabled = true } = options;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  // Tick to force a re-run of the effect on manual refresh.
  const [tick, setTick] = useState(0);

  const runFetch = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRefreshing(true);
    try {
      const result = await fetcherRef.current(controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      setData(result);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      if (controller.signal.aborted) return;
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (mountedRef.current && !controller.signal.aborted) {
        setRefreshing(false);
        setLoading(false);
      }
    }
  }, []);

  const refresh = useCallback(() => {
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // Reset loading state when the keyed dependency changes (e.g. market tab).
    setLoading(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;

    const schedule = () => {
      timerRef.current = setTimeout(async () => {
        if (cancelled) return;
        await runFetch();
        if (!cancelled) schedule();
      }, intervalMs);
    };

    // Immediate fetch, then schedule the interval.
    void runFetch().then(() => {
      if (!cancelled) schedule();
    });

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, enabled, tick, runFetch]);

  return { data, error, loading, refreshing, lastUpdated, refresh };
}

/**
 * A live mm:ss countdown to `targetIso`. Recomputes every second. Returns the
 * remaining seconds (clamped at 0); null when no target is known.
 */
export function useCountdown(targetIso: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!targetIso) return null;
  const target = new Date(targetIso).getTime();
  if (Number.isNaN(target)) return null;
  return Math.max(0, Math.floor((target - now) / 1000));
}
