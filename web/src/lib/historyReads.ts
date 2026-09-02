// Reading pages of a session's history and recording the window they form.
import type { QueryClient } from '@tanstack/svelte-query';
import type { Event, Page } from './types';
import { api } from './api';
import { deferTail, tailHeld } from './tailHold';
import {
  advanceOldest,
  fullKey,
  generation,
  mergeAnchored,
  seedBounds,
  windowBounds,
  windowKey,
} from './historyWindow';

/** Rows requested per read of a rendered window. */
export const WINDOW_PAGE_LIMIT = 200;

// The server's advertised events.max_page.
export const HISTORY_PAGE_LIMIT = 500;

/** Ids currently cached under a key. */
export function cachedIds(queryClient: QueryClient, key: readonly unknown[]): Set<string> {
  return new Set((queryClient.getQueryData<Event[]>(key) ?? []).map(e => e.id));
}

/** How a page of history is read. Injectable for tests. */
export type ReadPage = (
  sessionId: string,
  opts: { before?: string; limit: number; signal?: AbortSignal },
) => Promise<Page<Event>>;

const readPage: ReadPage = (sessionId, opts) => api.getSessionEvents(sessionId, opts);

// The newest-page read currently under way for each cache, with the
// generation and seam it was started in.
const coldReads = new Map<
  string,
  { generation: number; seam: number; waiting: number; page: Promise<Page<Event>> }
>();

// Identifies the current seam.
let seamEpoch = 0;

/** Begin a new seam. Reads started before it are no longer shared. */
export function newSeamEpoch(): void {
  seamEpoch += 1;
}

/**
 * Read the newest page of a session and return it.
 *
 * Joins a read already under way for the same cache generation and seam,
 * otherwise starts one. Rejects when `signal` aborts, without cancelling the
 * request for other callers; the shared read is dropped once no caller is
 * waiting on it. Recording the window's edge is left to `commitNewestWindow`.
 */
export async function fetchNewestWindow(
  sessionId: string,
  opts?: { signal?: AbortSignal; read?: ReadPage },
): Promise<Page<Event>> {
  const cacheKey = windowKey(sessionId);
  const captured = generation(cacheKey);
  const seam = seamEpoch;
  const shared = coldReads.get(cacheKey);
  const entry = shared && shared.generation === captured && shared.seam === seam
    ? shared
    : {
        generation: captured,
        seam,
        waiting: 0,
        page: (opts?.read ?? readPage)(sessionId, { limit: WINDOW_PAGE_LIMIT }),
      };
  if (entry !== shared) coldReads.set(cacheKey, entry);

  entry.waiting += 1;
  const leave = () => {
    entry.waiting -= 1;
    // Dropped once nobody is waiting on it.
    if (entry.waiting <= 0 && coldReads.get(cacheKey) === entry) coldReads.delete(cacheKey);
  };

  const signal = opts?.signal;
  try {
    if (!signal) return await entry.page;
    return await new Promise<Page<Event>>((resolve, reject) => {
      const abort = () => reject(signal.reason ?? new Error('aborted'));
      if (signal.aborted) {
        abort();
        return;
      }
      signal.addEventListener('abort', abort, { once: true });
      entry.page.then(
        page => { signal.removeEventListener('abort', abort); resolve(page); },
        error => { signal.removeEventListener('abort', abort); reject(error); },
      );
    });
  } finally {
    leave();
  }
}

/** Forget the newest-page read under way, so the next caller starts its own. */
export function discardSharedRead(sessionId: string): void {
  coldReads.delete(windowKey(sessionId));
}

/** Forget every shared read. */
export function clearSharedReads(): void {
  coldReads.clear();
}

/**
 * Record the window's edge from `page`.
 *
 * A no-op when `signal` is aborted, or when `captured` no longer matches the
 * cache's generation.
 */
export function commitNewestWindow(
  sessionId: string,
  page: Page<Event>,
  opts?: { signal?: AbortSignal; captured?: number },
): void {
  if (opts?.signal?.aborted) return;
  if (opts?.captured !== undefined && generation(windowKey(sessionId)) !== opts.captured) return;
  seedBounds(sessionId, page);
}

/**
 * Read the page before the window's oldest row and prepend it to the cache.
 *
 * A no-op when the window has no cursor or is at its beginning, and the write
 * is skipped when the generation moved or `signal` aborted while it was out.
 * `onPrepend` runs synchronously just before the write.
 */
export async function fetchOlderPage(
  queryClient: QueryClient,
  sessionId: string,
  opts?: { signal?: AbortSignal; read?: ReadPage; onPrepend?: () => void },
): Promise<void> {
  const edge = windowBounds(sessionId);
  if (!edge.hasMore || !edge.oldestCursor) return;

  const captured = generation(windowKey(sessionId));
  const page = await (opts?.read ?? readPage)(sessionId, {
    before: edge.oldestCursor,
    limit: WINDOW_PAGE_LIMIT,
    signal: opts?.signal,
  });
  if (generation(windowKey(sessionId)) !== captured || opts?.signal?.aborted) return;

  // Runs immediately before the write.
  opts?.onPrepend?.();
  queryClient.setQueryData<Event[]>(['session-events', sessionId], current =>
    mergeAnchored(current, page.items),
  );
  advanceOldest(sessionId, page);
}

/**
 * Merge rows into a cached history, deferring the write while the session's
 * list is holding its head.
 */
function commitRows(
  queryClient: QueryClient,
  sessionId: string,
  key: readonly unknown[],
  rows: Event[],
  stillWanted: () => boolean,
): void {
  const write = () => {
    // Asked again here: a deferred write runs later than its decision.
    if (!stillWanted()) return;
    if (!queryClient.getQueryState(key)) return;
    queryClient.setQueryData<Event[]>(key, current => mergeAnchored(current, rows));
  };
  if (tailHeld(sessionId)) deferTail(sessionId, write);
  else write();
}

/**
 * Page backwards from `before`, oldest-first.
 *
 * Stops at the last page, or at the first page holding an id in `stopAt`.
 */
export async function fetchSessionHistory(
  sessionId: string,
  opts?: { before?: string; signal?: AbortSignal; stopAt?: Set<string>; limit?: number; read?: ReadPage },
): Promise<Event[]> {
  let before = opts?.before;
  const pages: Event[][] = [];

  for (;;) {
    const page = await (opts?.read ?? readPage)(sessionId, {
      before,
      limit: opts?.limit ?? HISTORY_PAGE_LIMIT,
      signal: opts?.signal,
    });
    pages.unshift(page.items);
    if (!page.has_more || !page.next_cursor) break;
    if (opts?.stopAt?.size && page.items.some(e => opts.stopAt!.has(e.id))) break;
    before = page.next_cursor;
  }
  return pages.flat();
}

/**
 * Refetch a session's windowed history and merge it into the cache.
 *
 * Pages from `historyBefore`, or from the newest end when it is null, down to
 * a row the cache already holds. With nothing cached, reads the newest page
 * instead and records the window's edge from it.
 *
 * Writes nothing when the cache generation moved, or when `isCurrent` returns
 * false, at either point where it would write.
 */
export async function spliceSessionHistory(
  queryClient: QueryClient,
  sessionId: string,
  historyBefore: string | null,
  opts?: { read?: ReadPage; isCurrent?: () => boolean },
): Promise<void> {
  const key = ['session-events', sessionId];
  const captured = generation(windowKey(sessionId));
  const established = !!queryClient.getQueryData<Event[]>(key)?.length;
  const page = established
    ? null
    : await fetchNewestWindow(sessionId, { read: opts?.read });
  const window = established
    ? await fetchSessionHistory(sessionId, {
        before: historyBefore ?? undefined,
        stopAt: cachedIds(queryClient, key),
        limit: WINDOW_PAGE_LIMIT,
        read: opts?.read,
      })
    : (page as Page<Event>).items;

  const wanted = () =>
    generation(windowKey(sessionId)) === captured && (!opts?.isCurrent || opts.isCurrent());
  if (!wanted()) return;

  // Rows present now that were not when this read started: page from the
  // newest end down to them instead of keeping the page already read.
  if (page && queryClient.getQueryData<Event[]>(key)?.length) {
    const joined = await fetchSessionHistory(sessionId, {
      stopAt: cachedIds(queryClient, key),
      limit: WINDOW_PAGE_LIMIT,
      read: opts?.read,
    });
    if (!wanted()) return;
    commitRows(queryClient, sessionId, key, joined, wanted);
    return;
  }

  if (page) commitNewestWindow(sessionId, page, { captured });
  commitRows(queryClient, sessionId, key, window, wanted);
}

/** The same refetch against a session's whole-history cache. */
export async function spliceSessionFullHistory(
  queryClient: QueryClient,
  sessionId: string,
  historyBefore: string | null,
  opts?: { read?: ReadPage; isCurrent?: () => boolean },
): Promise<void> {
  const key = ['session-events-full', sessionId];
  const captured = generation(fullKey(sessionId));
  const window = await fetchSessionHistory(sessionId, {
    before: historyBefore ?? undefined,
    stopAt: cachedIds(queryClient, key),
    read: opts?.read,
  });

  const wanted = () =>
    generation(fullKey(sessionId)) === captured && (!opts?.isCurrent || opts.isCurrent());
  if (!wanted()) return;
  commitRows(queryClient, sessionId, key, window, wanted);
}
