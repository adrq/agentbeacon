<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { Event, SessionIdentity } from '../types';
  import { api } from '../api';
  import { buildThread } from '../threadMerge';
  import { sessionEventsFullQuery } from '../queries/executions';
  import Markdown from './Markdown.svelte';

  interface Props {
    sessionA: string;
    sessionB: string;
    sessionIdentity: Map<string, SessionIdentity>;
    sessionSettled: (sessionId: string) => boolean;
    sseActive: boolean;
    onclose: () => void;
  }

  let { sessionA, sessionB, sessionIdentity, sessionSettled, sseActive, onclose }: Props = $props();

  // Whole history, on its own key.
  const eventsAQuery = sessionEventsFullQuery(() => sessionA, () => sessionSettled(sessionA), () => sseActive);
  const eventsBQuery = sessionEventsFullQuery(() => sessionB, () => sessionSettled(sessionB), () => sseActive);

  let loading = $derived(eventsAQuery.isLoading || eventsBQuery.isLoading);
  let error = $derived(eventsAQuery.error?.message ?? eventsBQuery.error?.message ?? null);

  let slugA = $derived(sessionIdentity.get(sessionA)?.slug ?? sessionA.slice(0, 8));
  let slugB = $derived(sessionIdentity.get(sessionB)?.slug ?? sessionB.slice(0, 8));

  // Rows the user asked to see whole, keyed by event id.
  let fullPayloads = $state<Map<string, Event>>(new Map());
  let loadingFull = $state<Set<string>>(new Set());

  async function loadFull(executionId: string, eventId: string) {
    if (loadingFull.has(eventId) || fullPayloads.has(eventId)) return;
    loadingFull = new Set(loadingFull).add(eventId);
    try {
      const full = await api.getExecutionEvent(executionId, eventId);
      fullPayloads = new Map(fullPayloads).set(eventId, full);
    } catch {
      // Leave the affordance in place so the user can retry.
    } finally {
      const next = new Set(loadingFull);
      next.delete(eventId);
      loadingFull = next;
    }
  }

  /** Serve the full row once it has been fetched. */
  function resolveEvent(ev: Event): Event {
    return fullPayloads.get(ev.id) ?? ev;
  }

  let thread = $derived(buildThread(
    (eventsAQuery.data ?? []).map(resolveEvent),
    (eventsBQuery.data ?? []).map(resolveEvent),
    { sessionA, slugA, sessionB, slugB },
  ));
</script>

<div class="thread-view">
  <div class="thread-header">
    <button class="back-link" onclick={onclose}>← Back to {slugA}</button>
    <span class="thread-title">{slugA} ↔ {slugB}</span>
  </div>

  {#if loading}
    <div class="thread-loading">Loading thread...</div>
  {:else if error}
    <div class="thread-error">
      <span>Failed to load thread: {error}</span>
      <button class="retry-btn" onclick={() => { eventsAQuery.refetch(); eventsBQuery.refetch(); }}>Retry</button>
    </div>
  {:else if thread.length === 0}
    <div class="thread-empty">No messages found between {slugA} and {slugB}</div>
  {:else}
    <div class="thread-messages">
      {#each thread as entry (entry.eventId)}
        {@const isA = entry.senderSessionId === sessionA}
        <div class="thread-entry" class:side-a={isA} class:side-b={!isA}>
          <div class="entry-header">
            <span class="entry-sender">{entry.senderSlug}</span>
            <span class="entry-time">{entry.time}</span>
          </div>
          <div class="entry-body">
            {#each entry.parts as { part, omitted }}
              {#if 'text' in part && part.text}
                <Markdown text={part.text} />
              {:else if 'raw' in part && part.mediaType?.startsWith('image/')}
                {#if part.raw}
                  <img src="data:{part.mediaType};base64,{part.raw}" alt={part.filename ?? 'Image'} class="thread-image" />
                {:else if omitted}
                  <span class="thread-file">[image] {part.filename ?? 'image'} not delivered inline</span>
                {:else}
                  <span class="thread-file">[image] {part.filename ?? 'image'} is empty</span>
                {/if}
              {:else if 'url' in part || ('raw' in part && !part.mediaType?.startsWith('image/'))}
                <span class="thread-file">[file] {part.filename ?? 'file'}</span>
              {:else if 'data' in part}
                <span class="thread-file">[data]</span>
              {/if}
            {/each}
            {#if entry.unsupported}
              <div class="truncated-notice" data-testid="thread-unsupported">
                <span class="thread-file">This event uses a newer format.</span>
                <button
                  type="button"
                  class="load-full-btn"
                  disabled={loadingFull.has(entry.eventId)}
                  onclick={() => loadFull(entry.executionId, entry.eventId)}
                >
                  {loadingFull.has(entry.eventId) ? 'Loading…' : 'Load full content'}
                </button>
              </div>
            {/if}
            {#if entry.truncated}
              <div class="truncated-notice">
                <span class="thread-file">This message was shortened for delivery.</span>
                <button
                  type="button"
                  class="load-full-btn"
                  disabled={loadingFull.has(entry.eventId)}
                  onclick={() => loadFull(entry.executionId, entry.eventId)}
                >
                  {loadingFull.has(entry.eventId) ? 'Loading…' : 'Load full content'}
                </button>
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
    <div class="thread-footer">
      {thread.length} message{thread.length === 1 ? '' : 's'}
    </div>
  {/if}
</div>

<style>
  .thread-view {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  .thread-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid hsl(var(--border));
    flex-shrink: 0;
  }

  .back-link {
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.8125rem;
    font-weight: 400;
    cursor: pointer;
    padding: 0;
  }

  .back-link:hover {
    color: hsl(var(--foreground));
  }

  .thread-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .thread-loading,
  .thread-empty {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .thread-error {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
  }

  .retry-btn {
    border: 1px solid hsl(var(--border));
    background: transparent;
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius-sm);
    cursor: pointer;
  }

  .retry-btn:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .thread-messages {
    flex: 1;
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    overflow-y: auto;
  }

  .thread-entry {
    padding: 0.5rem 0.75rem;
    border-left: 3px solid transparent;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.15);
  }

  .thread-entry.side-a {
    border-left-color: hsl(var(--status-working));
  }

  .thread-entry.side-b {
    border-left-color: hsl(var(--muted-foreground) / 0.3);
  }

  .entry-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 0.25rem;
  }

  .entry-sender {
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .entry-time {
    font-family: var(--font-mono);
    font-size: 0.625rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: hsl(var(--muted-foreground));
  }

  .entry-body {
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
  }

  .thread-file {
    display: inline-block;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    padding: 0.125rem 0.375rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    margin-top: 0.25rem;
  }

  .truncated-notice {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.25rem;
    padding: 0.375rem 0.5rem;
    border: 1px dashed hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--muted) / 0.4);
  }

  .truncated-notice .thread-file {
    border: none;
    padding: 0;
    margin-top: 0;
  }

  .load-full-btn {
    font-size: 12px;
    padding: 0.15rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    cursor: pointer;
  }

  .load-full-btn:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .thread-image {
    max-width: 100%;
    max-height: 400px;
    border-radius: var(--radius-sm);
    margin-top: 0.25rem;
  }

  .thread-footer {
    padding: 0.375rem 1rem;
    border-top: 1px solid hsl(var(--border));
    font-family: var(--font-mono);
    font-size: 0.625rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }
</style>
