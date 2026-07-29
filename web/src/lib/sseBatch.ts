// Batches SSE cache writes and classifies event newness. Kept dependency-free
// (cache + scheduler injected) so it runs standalone in fast unit tests.

export interface BatchEvent {
  id: number;
  session_id: string | null;
}

// Cache writes are delegated so the batcher stays free of TanStack/Svelte.
export interface BatchCache<E extends BatchEvent> {
  // Current cached events for a session key, used to seed the per-session id set
  // and to dedupe at flush time.
  getExisting(sessionKey: string | null): E[] | undefined;
  // Append events not already present under this key, in one write.
  appendNew(sessionKey: string | null, events: E[]): void;
}

// Injected so tests drive flush timing deterministically; production wires
// requestAnimationFrame plus a latency timer.
export interface FlushScheduler {
  request(flush: () => void): void;
  cancel(): void;
}

// Classifies each event as new (first delivery) vs already-known, buffers new
// events per session, and flushes them to the cache in one write per session.
// Newness is decided synchronously at enqueue so callers can run side effects in
// delivery order; only the cache write is deferred.
export class SSEBatcher<E extends BatchEvent> {
  private readonly seen = new Map<string | null, Set<number>>();
  private pending = new Map<string | null, E[]>();
  private pendingCount = 0;

  constructor(
    private readonly cache: BatchCache<E>,
    private readonly scheduler: FlushScheduler,
    private readonly sizeCap = 500,
  ) {}

  // Returns true when the event is seen for the first time on this connection.
  // The caller runs side effects only when true; usage accumulation runs
  // regardless (it carries its own dedupe).
  enqueue(event: E): boolean {
    const key = event.session_id;
    let ids = this.seen.get(key);
    if (!ids) {
      ids = new Set((this.cache.getExisting(key) ?? []).map((e) => e.id));
      this.seen.set(key, ids);
    }
    if (ids.has(event.id)) return false;
    ids.add(event.id);

    let bucket = this.pending.get(key);
    if (!bucket) {
      bucket = [];
      this.pending.set(key, bucket);
    }
    bucket.push(event);
    this.pendingCount++;

    if (this.pendingCount >= this.sizeCap) {
      this.flush();
    } else {
      this.scheduler.request(() => this.flush());
    }
    return true;
  }

  flush(): void {
    this.scheduler.cancel();
    if (this.pendingCount === 0) return;
    const batch = this.pending;
    this.pending = new Map();
    this.pendingCount = 0;
    for (const [key, events] of batch) {
      this.cache.appendNew(key, events);
    }
  }

  // Drain buffered writes synchronously and release state; call on teardown.
  dispose(): void {
    this.flush();
    this.scheduler.cancel();
    this.seen.clear();
  }
}

// Builds the SSE stream URL for a given cursor. Extracted for unit coverage of
// the reconnect-from-latest-cursor behavior.
export function streamUrl(base: string, since: number): string {
  return since ? `${base}?since=${since}` : base;
}
