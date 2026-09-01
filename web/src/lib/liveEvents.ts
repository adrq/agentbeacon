// Live events for a session with no cached history, and per-session release
// counts.
import type { Event } from './types';

const HOLD_CAP = 500;

const held = new Map<string, Event[]>();
const arrivals = new Map<string, number>();

/** Hold live events for a session whose history is not cached yet. */
export function holdLiveEvents(sessionId: string, events: Event[]): void {
  const current = held.get(sessionId);
  const next = current ? [...current, ...events] : [...events];
  if (next.length > HOLD_CAP) {
    // Released rather than grown.
    held.delete(sessionId);
    arrivals.set(sessionId, (arrivals.get(sessionId) ?? 0) + 1);
    return;
  }
  held.set(sessionId, next);
}

/** Live events held for a session, oldest first. */
export function heldLiveEvents(sessionId: string): Event[] {
  return held.get(sessionId) ?? [];
}

/** How many times this session's hold has been released. */
export function releaseCount(sessionId: string): number {
  return arrivals.get(sessionId) ?? 0;
}

/** Release everything held and every mark. */
export function clearHeldLiveEvents(): void {
  held.clear();
  arrivals.clear();
}

/**
 * Merge a fetched window with what is already known.
 *
 * `fetched` keeps its server order. Held live events and cached entries the
 * fetch did not cover follow it, deduped by id.
 */
export function unionWithKnown(
  fetched: Event[],
  cached: Event[] | undefined,
  sessionId: string,
): Event[] {
  const trailing: Event[] = [];
  const seen = new Set(fetched.map(e => e.id));
  for (const event of [...(cached ?? []), ...heldLiveEvents(sessionId)]) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    trailing.push(event);
  }
  return trailing.length ? [...fetched, ...trailing] : fetched;
}
