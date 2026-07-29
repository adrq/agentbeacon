import { test, expect } from '@playwright/test';
import { SSEBatcher, streamUrl, type BatchCache, type FlushScheduler } from '../../src/lib/sseBatch';

interface E { id: number; session_id: string | null }
const ev = (id: number, session_id: string | null = 's1'): E => ({ id, session_id });

class FakeCache implements BatchCache<E> {
  store = new Map<string | null, E[]>();
  appendCalls = 0;
  getExisting(key: string | null) { return this.store.get(key); }
  appendNew(key: string | null, events: E[]) {
    this.appendCalls++;
    const old = this.store.get(key) ?? [];
    const ids = new Set(old.map(e => e.id));
    this.store.set(key, [...old, ...events.filter(e => !ids.has(e.id))]);
  }
}

class FakeScheduler implements FlushScheduler {
  pending: (() => void) | null = null;
  requests = 0;
  request(flush: () => void) { this.pending = flush; this.requests++; }
  cancel() { this.pending = null; }
  fire() { const f = this.pending; this.pending = null; f?.(); }
}

// These are fast, deterministic unit tests for the pure batching module: no
// browser navigation and no backend. They exercise the SSE replay and
// batching failure modes.

test('newness is decided synchronously at enqueue, before any flush', () => {
  const b = new SSEBatcher<E>(new FakeCache(), new FakeScheduler());
  expect(b.enqueue(ev(1))).toBe(true);
  expect(b.enqueue(ev(1))).toBe(false); // duplicate within the same batch
});

test('REST-race: event supplied by REST before flush is not appended twice', () => {
  const cache = new FakeCache();
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched);
  // Seed empty: first event classified new and buffered.
  expect(b.enqueue(ev(1))).toBe(true);
  // REST resolves and populates the cache with the same event before the flush.
  cache.store.set('s1', [ev(1)]);
  sched.fire();
  expect(cache.store.get('s1')).toEqual([ev(1)]); // exactly one row
});

test('in-batch duplicates append one row and one appendNew call', () => {
  const cache = new FakeCache();
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched);
  b.enqueue(ev(1));
  b.enqueue(ev(1));
  b.enqueue(ev(2));
  sched.fire();
  expect(cache.store.get('s1')).toEqual([ev(1), ev(2)]);
  expect(cache.appendCalls).toBe(1);
});

test('event already present in seeded cache is not new and not buffered', () => {
  const cache = new FakeCache();
  cache.store.set('s1', [ev(1)]);
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched);
  expect(b.enqueue(ev(1))).toBe(false);
  expect(sched.requests).toBe(0); // nothing buffered → no flush scheduled
});

test('exec-switch: dispose drains a queued buffer and cancels the scheduler', () => {
  const cache = new FakeCache();
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched);
  b.enqueue(ev(1));
  expect(sched.pending).not.toBeNull(); // rAF queued, not yet fired
  b.dispose();
  expect(cache.store.get('s1')).toEqual([ev(1)]); // drained synchronously
  expect(sched.pending).toBeNull(); // scheduler cancelled
});

test('close-with-buffer flushes 1 queued event', () => {
  const cache = new FakeCache();
  const b = new SSEBatcher<E>(cache, new FakeScheduler());
  b.enqueue(ev(1));
  b.flush(); // simulates onDisconnected hook
  expect(cache.store.get('s1')).toEqual([ev(1)]);
});

test('close-with-buffer flushes 499 queued events without hitting the cap', () => {
  const cache = new FakeCache();
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched, 500);
  for (let i = 1; i <= 499; i++) b.enqueue(ev(i));
  expect(cache.appendCalls).toBe(0); // below cap: not auto-flushed
  b.flush();
  expect(cache.store.get('s1')?.length).toBe(499);
});

test('size cap forces an immediate flush at the threshold', () => {
  const cache = new FakeCache();
  const b = new SSEBatcher<E>(cache, new FakeScheduler(), 500);
  for (let i = 1; i <= 500; i++) b.enqueue(ev(i));
  expect(cache.store.get('s1')?.length).toBe(500); // flushed on reaching cap
});

test('events are bucketed and flushed per session, including a null session', () => {
  const cache = new FakeCache();
  const sched = new FakeScheduler();
  const b = new SSEBatcher<E>(cache, sched);
  b.enqueue(ev(1, 's1'));
  b.enqueue(ev(2, 's2'));
  b.enqueue(ev(3, null));
  sched.fire();
  expect(cache.store.get('s1')).toEqual([ev(1, 's1')]);
  expect(cache.store.get('s2')).toEqual([ev(2, 's2')]);
  expect(cache.store.get(null)).toEqual([ev(3, null)]);
});

test('streamUrl resumes from the latest cursor', () => {
  const base = '/api/executions/abc/events/stream';
  expect(streamUrl(base, 0)).toBe(base);
  expect(streamUrl(base, 42)).toBe(`${base}?since=42`);
});
