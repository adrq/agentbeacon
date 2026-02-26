<script lang="ts">
  import { tick } from 'svelte';
  import type { Event, Agent, SessionSummary, AgentType } from '../types';
  import { isMessagePayload, isStateChangePayload, isEscalateData, isDelegateData, isHandoffResultData, isTurnCompleteData, isPlanData } from '../types';
  import { normalizeDataPart, type NormalizedToolCall, type NormalizedToolResult, type NormalizedThinking } from '../normalize';
  import { api } from '../api';
  import Markdown from './Markdown.svelte';
  import ToolCallCard from './renderers/ToolCallCard.svelte';
  import ToolResultCard from './renderers/ToolResultCard.svelte';
  import ThinkingBlock from './renderers/ThinkingBlock.svelte';
  import DataFallback from './renderers/DataFallback.svelte';
  import ErrorPanel from './renderers/ErrorPanel.svelte';

  interface Props {
    events: Event[];
    agents: Agent[];
    sessions: SessionSummary[];
    sessionId: string | null;
  }

  let { events, agents, sessions, sessionId }: Props = $props();
  let scrollContainer: HTMLDivElement | undefined = $state(undefined);
  let shouldAutoScroll = $state(true);
  let messageText = $state('');
  let sending = $state(false);
  let sendError: string | null = $state(null);

  // The viewed session determines whether the input is enabled
  let viewedSession = $derived(sessions.find(s => s.id === sessionId) ?? null);
  let inputEnabled = $derived(
    viewedSession?.status === 'input-required' || viewedSession?.status === 'working'
  );
  let canSend = $derived(inputEnabled && messageText.trim().length > 0 && !sending);

  async function handleSend() {
    if (!sessionId || !canSend) return;
    sending = true;
    sendError = null;
    try {
      await api.postMessage(sessionId, messageText.trim());
      messageText = '';
    } catch (e) {
      sendError = e instanceof Error ? e.message : 'Failed to send';
    } finally {
      sending = false;
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleScroll() {
    if (!scrollContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  $effect(() => {
    const _len = parsed.length; // dependency: re-run when entries change
    if (shouldAutoScroll && scrollContainer) {
      tick().then(() => {
        if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
      });
    }
  });

  function formatTime(iso: string): string {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  // Determine the primary agent for context
  let leadSession = $derived(sessions.find(s => !s.parent_session_id));

  type ChatEntry =
    | { type: 'agent'; text: string; agentLabel: string; time: string; key: string }
    | { type: 'user'; text: string; time: string; key: string }
    | { type: 'state'; text: string; time: string; key: string }
    | { type: 'tool'; icon: string; text: string; time: string; key: string }
    | { type: 'tool_call'; data: NormalizedToolCall; time: string; key: string }
    | { type: 'tool_result'; data: NormalizedToolResult; time: string; key: string }
    | { type: 'thinking'; data: NormalizedThinking; time: string; key: string }
    | { type: 'data_fallback'; data: Record<string, unknown>; time: string; key: string }
    | { type: 'error'; message: string; stderr?: string; time: string; key: string }
    | { type: 'fyi'; text: string; time: string; key: string };

  function resolveAgentType(sessionId: string | null): AgentType {
    const session = sessions.find(s => s.id === sessionId);
    const agent = agents.find(a => a.id === session?.agent_id);
    return agent?.agent_type ?? 'acp';
  }

  function parseEntries(evs: Event[]): ChatEntry[] {
    const entries: ChatEntry[] = [];
    const agentLabel = leadSession ? agentName(leadSession.agent_id) : 'Agent';
    let seq = 0;

    for (const ev of evs) {
      const time = formatTime(ev.created_at);

      if (isStateChangePayload(ev.payload)) {
        const p = ev.payload;
        if (p.to === 'failed') {
          entries.push({
            type: 'error',
            message: p.error ?? (p.from ? `Execution failed (was ${p.from})` : 'Execution failed'),
            stderr: p.stderr,
            time,
            key: `${ev.id}-err-${seq++}`,
          });
        }
        entries.push({
          type: 'state',
          text: p.from ? `${p.from} \u2192 ${p.to}` : `started \u2192 ${p.to}`,
          time,
          key: `${ev.id}-${seq++}`,
        });
        continue;
      }

      if (isMessagePayload(ev.payload)) {
        const msg = ev.payload;
        const agentType = resolveAgentType(ev.session_id);

        for (const part of msg.parts) {
          if (part.kind === 'data') {
            const d = part.data as Record<string, unknown>;

            // Platform events: route to existing renderers
            if (isEscalateData(d as unknown as import('../types').DataPartPayload)) {
              const ask = d as unknown as import('../types').EscalateData;
              if (ask.batch_index > 0) continue;
              if (ask.importance === 'fyi') {
                entries.push({ type: 'fyi', text: ask.question, time, key: `${ev.id}-${seq++}` });
              } else {
                const text = ask.batch_size > 1
                  ? `${ask.question}\n(+ ${ask.batch_size - 1} more question${ask.batch_size > 2 ? 's' : ''})`
                  : ask.question;
                entries.push({ type: 'tool', icon: '\u26A0', text, time, key: `${ev.id}-${seq++}` });
              }
              continue;
            }
            if (isDelegateData(d as unknown as import('../types').DataPartPayload)) {
              const del = d as unknown as import('../types').DelegateData;
              entries.push({ type: 'tool', icon: '\u2192', text: `Delegated to ${del.agent}`, time, key: `${ev.id}-${seq++}` });
              continue;
            }
            if (isHandoffResultData(d as unknown as import('../types').DataPartPayload)) {
              const hr = d as unknown as import('../types').HandoffResultData;
              entries.push({ type: 'tool', icon: '\u2713', text: `Child completed: ${hr.message}`, time, key: `${ev.id}-${seq++}` });
              continue;
            }
            if (isTurnCompleteData(d as unknown as import('../types').DataPartPayload)) {
              const tc = d as unknown as import('../types').TurnCompleteData;
              entries.push({ type: 'tool', icon: '\u21A9', text: `Child reported: ${tc.message}`, time, key: `${ev.id}-${seq++}` });
              continue;
            }

            // Normalize SDK/ACP data parts
            const norm = normalizeDataPart(agentType, d);
            switch (norm.normalized) {
              case 'tool_call':
                entries.push({ type: 'tool_call', data: norm, time, key: `${ev.id}-${seq++}` });
                break;
              case 'tool_result':
                entries.push({ type: 'tool_result', data: norm, time, key: `${ev.id}-${seq++}` });
                break;
              case 'thinking':
                entries.push({ type: 'thinking', data: norm, time, key: `${ev.id}-${seq++}` });
                break;
              case 'unknown': {
                const rawType = norm.raw.type as string | undefined;
                if (rawType === 'plan' && isPlanData(norm.raw as unknown as import('../types').DataPartPayload)) {
                  const plan = norm.raw as unknown as import('../types').PlanData;
                  entries.push({ type: 'tool', icon: '\u2630', text: `Plan (${plan.entries.length} steps)`, time, key: `${ev.id}-${seq++}` });
                } else if (rawType === 'current_mode_update') {
                  entries.push({ type: 'tool', icon: '\u25A1', text: `[mode_change]`, time, key: `${ev.id}-${seq++}` });
                } else if (rawType === 'available_commands_update') {
                  entries.push({ type: 'tool', icon: '\u25A1', text: `[available_commands]`, time, key: `${ev.id}-${seq++}` });
                } else {
                  entries.push({ type: 'data_fallback', data: norm.raw, time, key: `${ev.id}-${seq++}` });
                }
                break;
              }
            }
          } else if (part.kind === 'file') {
            const name = 'file' in part && typeof part.file === 'object' && part.file && 'name' in part.file
              ? (part.file as { name: string }).name : 'file';
            entries.push({ type: 'tool', icon: '\u25A1', text: `[file] ${name}`, time, key: `${ev.id}-${seq++}` });
          } else if (part.kind === 'text') {
            const text = part.text as string;
            if (msg.role === 'user') {
              entries.push({ type: 'user', text, time, key: `${ev.id}-${seq++}` });
            } else {
              entries.push({ type: 'agent', text, agentLabel, time, key: `${ev.id}-${seq++}` });
            }
          } else {
            const label = part.kind;
            const detail = 'text' in part && typeof part.text === 'string'
              ? part.text
              : 'name' in part && typeof part.name === 'string'
                ? part.name
                : '';
            entries.push({ type: 'tool', icon: '\u25A1', text: detail ? `[${label}] ${detail}` : `[${label}]`, time, key: `${ev.id}-${seq++}` });
          }
        }
      }
    }
    return entries;
  }

  let parsed = $derived(parseEntries(events));
</script>

<div class="chat-container">
<div class="chat-scroll scroll-thin" bind:this={scrollContainer} onscroll={handleScroll}>
  {#if parsed.length === 0}
    <div class="chat-empty">No messages yet</div>
  {:else}
    <div class="chat-messages">
      {#each parsed as entry (entry.key)}
        {#if entry.type === 'agent'}
          <div class="chat-row agent-row">
            <div class="bubble agent-bubble">
              <div class="bubble-header">{entry.agentLabel}</div>
              <div class="bubble-text"><Markdown text={entry.text} /></div>
              <div class="bubble-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'user'}
          <div class="chat-row user-row">
            <div class="bubble user-bubble">
              <div class="bubble-text">{entry.text}</div>
              <div class="bubble-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'state'}
          <div class="chat-row state-row">
            <span class="state-text">{entry.text}</span>
          </div>
        {:else if entry.type === 'tool_call'}
          <div class="chat-row tool-row">
            <ToolCallCard data={entry.data} />
          </div>
        {:else if entry.type === 'tool_result'}
          <div class="chat-row tool-row">
            <ToolResultCard data={entry.data} />
          </div>
        {:else if entry.type === 'thinking'}
          <div class="chat-row tool-row">
            <ThinkingBlock data={entry.data} />
          </div>
        {:else if entry.type === 'data_fallback'}
          <div class="chat-row tool-row">
            <DataFallback data={entry.data} />
          </div>
        {:else if entry.type === 'error'}
          <div class="chat-row tool-row">
            <ErrorPanel message={entry.message} stderr={entry.stderr} />
          </div>
        {:else if entry.type === 'tool'}
          <div class="chat-row tool-row">
            <div class="tool-card">
              <span class="tool-icon">{entry.icon}</span>
              <span class="tool-text">{entry.text}</span>
              <span class="tool-time">{entry.time}</span>
            </div>
          </div>
        {:else if entry.type === 'fyi'}
          <div class="chat-row fyi-row">
            <div class="fyi-card">
              <span class="fyi-icon">{'\u2139'}</span>
              <span class="fyi-text">{entry.text}</span>
            </div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<div class="chat-input-area">
  {#if sendError}
    <div class="send-error">{sendError}</div>
  {/if}
  <div class="chat-input-row">
    <textarea
      class="chat-input"
      placeholder={viewedSession?.status === 'input-required' ? 'Type a message...' : viewedSession?.status === 'working' ? 'Message will be delivered after current step...' : 'Agent is working...'}
      disabled={!inputEnabled || sending}
      bind:value={messageText}
      onkeydown={handleKeydown}
      rows="1"
    ></textarea>
    <button
      class="send-btn"
      disabled={!canSend}
      onclick={handleSend}
      aria-label="Send message"
    >
      {#if sending}
        ...
      {:else}
        Send
      {/if}
    </button>
  </div>
</div>
</div>

<style>
  .chat-container {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }

  .chat-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 0.5rem 1rem 1rem;
  }

  .chat-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
  }

  .chat-messages {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .chat-row {
    display: flex;
  }

  .agent-row {
    justify-content: flex-start;
  }

  .user-row {
    justify-content: flex-end;
  }

  .state-row {
    justify-content: center;
  }

  .tool-row, .fyi-row {
    justify-content: center;
  }

  .bubble {
    max-width: 75%;
    padding: 0.5rem 0.75rem;
    border-radius: 0.75rem;
    font-size: 0.8125rem;
    line-height: 1.5;
    word-break: break-word;
  }

  .agent-bubble {
    width: 100%;
    background: hsl(var(--muted));
    color: hsl(var(--foreground));
    border-bottom-left-radius: 0.25rem;
  }

  .user-bubble {
    background: hsl(var(--primary) / 0.15);
    color: hsl(var(--foreground));
    border-bottom-right-radius: 0.25rem;
  }

  .bubble-header {
    font-size: 0.6875rem;
    font-weight: 600;
    color: hsl(var(--primary));
    margin-bottom: 0.125rem;
  }

  .user-bubble .bubble-text {
    white-space: pre-wrap;
  }

  .bubble-time {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
    text-align: right;
    font-family: var(--font-mono);
  }

  .state-text {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    padding: 0.125rem 0.5rem;
  }

  .tool-card {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 0.625rem;
    border-radius: 0.375rem;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.3);
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    max-width: 85%;
  }

  .tool-icon {
    flex-shrink: 0;
    font-size: 0.75rem;
    color: hsl(var(--status-attention));
  }

  .tool-text {
    white-space: pre-wrap;
    word-break: break-word;
  }

  .tool-time {
    flex-shrink: 0;
    font-size: 0.625rem;
    font-family: var(--font-mono);
    opacity: 0.7;
  }

  .fyi-card {
    display: inline-flex;
    align-items: flex-start;
    gap: 0.375rem;
    padding: 0.375rem 0.625rem;
    border-radius: 0.375rem;
    background: hsl(var(--status-working) / 0.08);
    border: 1px solid hsl(var(--status-working) / 0.2);
    font-size: 0.75rem;
    color: hsl(var(--foreground));
    max-width: 85%;
  }

  .fyi-icon {
    color: hsl(var(--status-working));
    flex-shrink: 0;
  }

  .fyi-text {
    white-space: pre-wrap;
    word-break: break-word;
  }

  .chat-input-area {
    flex-shrink: 0;
    padding: 0.5rem 1rem 0.75rem;
    border-top: 1px solid hsl(var(--border));
    background: hsl(var(--card));
  }

  .send-error {
    padding: 0.25rem 0.5rem;
    margin-bottom: 0.375rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.75rem;
  }

  .chat-input-row {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
  }

  .chat-input {
    flex: 1;
    padding: 0.5rem 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.5rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: inherit;
    line-height: 1.5;
    resize: none;
    outline: none;
    transition: border-color 0.15s;
  }

  .chat-input:focus {
    border-color: hsl(var(--primary));
  }

  .chat-input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .chat-input::placeholder {
    color: hsl(var(--muted-foreground));
  }

  .send-btn {
    padding: 0.5rem 1rem;
    border-radius: 0.5rem;
    border: none;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    font-size: 0.8125rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    filter: brightness(1.1);
  }

  .send-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
