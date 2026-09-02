// Merging a read of history into a cached one, and the paging bounds a
// session's cached window currently carries.
import { writable, type Readable } from 'svelte/store';
import type { Event } from './types';
import { heldLiveEvents } from './liveEvents';

/** How far back a session's cached window reaches. */
export interface WindowBounds {
  /** Cursor for the next older page. Null before the first read and at the end. */
  oldestCursor: string | null;
  /** False once the server reported no more pages. Never returns to true. */
  hasMore: boolean;
}

interface PageMeta {
  next_cursor: string | null;
  has_more: boolean;
  items?: readonly unknown[];
}

/** Bounds, plus whether any row has ever been recorded for the session. */
interface WindowRecord extends WindowBounds {
  empty: boolean;
}

const bounds = new Map<string, WindowRecord>();
const generations = new Map<string, number>();

const version = writable(0);
/** Increments on every bounds change. */
export const windowVersion: Readable<number> = { subscribe: version.subscribe };

function bump() {
  version.update(n => n + 1);
}

/** Bounds for a session, defaulting to "nothing read yet". */
export function windowBounds(sessionId: string): WindowBounds {
  const record = bounds.get(sessionId);
  return record
    ? { oldestCursor: record.oldestCursor, hasMore: record.hasMore }
    : { oldestCursor: null, hasMore: true };
}

/**
 * Record the bounds of a session's first page.
 *
 * A no-op once bounds exist, except when the recorded window is empty and at
 * its beginning and `page` reports more: those bounds are replaced.
 */
export function seedBounds(sessionId: string, page: PageMeta): void {
  const current = bounds.get(sessionId);
  const staleEmpty = current?.empty === true && current.hasMore === false;
  if (current && !(staleEmpty && page.has_more)) return;
  bounds.set(sessionId, {
    oldestCursor: page.next_cursor,
    hasMore: page.has_more,
    // A page that does not report its items counts as holding rows.
    empty: page.items !== undefined && page.items.length === 0,
  });
  bump();
}

/** Move the oldest edge outward. A no-op with no bounds, or once at the beginning. */
export function advanceOldest(sessionId: string, page: PageMeta): void {
  const current = bounds.get(sessionId);
  if (!current || !current.hasMore) return;
  bounds.set(sessionId, {
    oldestCursor: page.next_cursor,
    hasMore: current.hasMore && page.has_more,
    empty: current.empty && page.items !== undefined && page.items.length === 0,
  });
  bump();
}

/** Keys naming the two caches a session's rows can be written to. */
export const windowKey = (sessionId: string) => `session-events:${sessionId}`;
export const fullKey = (sessionId: string) => `session-events-full:${sessionId}`;

/** Current generation of one cache. */
export function generation(cacheKey: string): number {
  return generations.get(cacheKey) ?? 0;
}

/** Advance one cache's generation, and drop the bounds under a window key. */
export function dropCache(cacheKey: string): void {
  generations.set(cacheKey, generation(cacheKey) + 1);
  const sessionId = cacheKey.startsWith('session-events-full:')
    ? null
    : cacheKey.slice('session-events:'.length);
  if (sessionId !== null) bounds.delete(sessionId);
  bump();
}

/** Advance the generation of a session's window key and drop its bounds. */
export function resetWindow(sessionId: string): void {
  dropCache(windowKey(sessionId));
}

export function clearWindows(): void {
  for (const cacheKey of new Set([
    ...[...bounds.keys()].map(windowKey),
    ...generations.keys(),
  ])) {
    generations.set(cacheKey, generation(cacheKey) + 1);
  }
  bounds.clear();
  bump();
}

/**
 * Merge `window` into `cached` at the first row the two share, comparing ids
 * for equality only.
 *
 * Cached rows before that row stay in front of the window and those from it
 * onwards stay behind. With no row in common the window goes in front.
 */
export function mergeAnchored(cached: Event[] | undefined, window: Event[]): Event[] {
  const current = cached ?? [];
  const windowIds = new Set(window.map(e => e.id));
  const outside = (events: Event[]) => events.filter(e => !windowIds.has(e.id));
  const anchor = current.findIndex(e => windowIds.has(e.id));
  if (anchor === -1) return [...window, ...outside(current)];
  return [
    ...outside(current.slice(0, anchor)),
    ...window,
    ...outside(current.slice(anchor)),
  ];
}

/**
 * Append the session's held live rows that `events` does not account for.
 *
 * `incorporated` names ids the destination cache already holds; `heldBefore`
 * names the rows held when the read began. Returns `events` unchanged when
 * there is nothing to append.
 */
export function appendHeld(
  events: Event[],
  sessionId: string,
  opts?: { incorporated?: ReadonlySet<string>; heldBefore?: ReadonlySet<string> },
): Event[] {
  const holds = heldLiveEvents(sessionId);
  if (holds.length === 0) return events;

  const seen = new Set(events.map(e => e.id));
  const visible = (id: string) => seen.has(id) || opts?.incorporated?.has(id) === true;

  let anchor = -1;
  for (let i = holds.length - 1; i >= 0; i -= 1) {
    if (visible(holds[i].id)) {
      anchor = i;
      break;
    }
  }

  const trailing: Event[] = [];
  holds.forEach((event, index) => {
    if (visible(event.id)) return;
    if (anchor >= 0 ? index < anchor : events.length > 0 && opts?.heldBefore?.has(event.id)) return;
    if (seen.has(event.id)) return;
    seen.add(event.id);
    trailing.push(event);
  });
  return trailing.length ? [...events, ...trailing] : events;
}
