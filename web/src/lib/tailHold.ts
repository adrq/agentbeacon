// Deferring writes that add rows to the end of a session's history while its
// list is holding its head.

const held = new Set<string>();
const deferred = new Map<string, (() => void)[]>();

/** Whether writes to the end of this session's history are being deferred. */
export function tailHeld(sessionId: string): boolean {
  return held.has(sessionId);
}

/** Begin deferring writes to the end of this session's history. */
export function holdTail(sessionId: string): void {
  held.add(sessionId);
}

/**
 * Stop deferring, and run what was deferred in the order it was given.
 *
 * A no-op for a session that is not held.
 */
export function releaseTail(sessionId: string): void {
  if (!held.delete(sessionId)) return;
  const writes = deferred.get(sessionId) ?? [];
  deferred.delete(sessionId);
  for (const write of writes) write();
}

/** Queue a write to run when this session is released. */
export function deferTail(sessionId: string, write: () => void): void {
  deferred.set(sessionId, [...(deferred.get(sessionId) ?? []), write]);
}

/** Release every held session, running what each deferred. */
export function releaseAllTails(): void {
  for (const sessionId of [...held]) releaseTail(sessionId);
}
