<script lang="ts">
  import { tick } from 'svelte';
  import type { Event, Agent, SessionSummary, AgentType, TodoItem } from '../types';
  import { isMessagePayload, isStateChangePayload, isEscalateData, isDelegateData, isTurnCompleteData, isPlanData } from '../types';
  import { normalizeDataPart, type NormalizedToolCall, type NormalizedToolResult, type NormalizedThinking } from '../normalize';
  import { api } from '../api';
  import Markdown from './Markdown.svelte';
  import ToolGroup from './renderers/ToolGroup.svelte';
  import ToolStream from './renderers/ToolStream.svelte';
  import ThinkingBlock from './renderers/ThinkingBlock.svelte';
  import DataFallback from './renderers/DataFallback.svelte';
  import ErrorPanel from './renderers/ErrorPanel.svelte';
  import TodoChecklist from './renderers/TodoChecklist.svelte';
  import TodoPanel from './TodoPanel.svelte';
  import { EVENT_FILTER_GROUPS, EVENT_FILTER_PILLS, type EventFilter } from '../eventFilterGroups';

  interface Props {
    events: Event[];
    agents: Agent[];
    sessions: SessionSummary[];
    sessionId: string | null;
    ephemeralText?: string;
    ephemeralThinking?: { text: string; startedAt: string } | null;
    settledThinkingDuration?: { durationMs: number; startedAt: string } | null;
    eventFilter?: EventFilter;
    onfilterchange?: (filter: EventFilter) => void;
  }

  let { events, agents, sessions, sessionId, ephemeralText = '', ephemeralThinking = null, settledThinkingDuration = null, eventFilter = 'all', onfilterchange }: Props = $props();
  let scrollContainer: HTMLDivElement | undefined = $state(undefined);
  let shouldAutoScroll = $state(true);
  let messageText = $state('');
  let sending = $state(false);
  let sendError: string | null = $state(null);
  let textareaEl: HTMLTextAreaElement;
  let plusMenuOpen = $state(false);
  let attachments = $state<{ file: File; preview: string }[]>([]);
  let fileInputEl: HTMLInputElement;
  const MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5MB
  const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

  function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result as string;
        resolve(dataUrl.split(',')[1]);
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  function addFiles(files: FileList | File[]) {
    for (const file of files) {
      if (!ACCEPTED_TYPES.includes(file.type)) {
        sendError = `Unsupported format: ${file.type}. Use JPEG, PNG, WebP, or GIF.`;
        continue;
      }
      if (file.size > MAX_IMAGE_SIZE) {
        sendError = `${file.name} exceeds 5MB limit.`;
        continue;
      }
      attachments = [...attachments, { file, preview: URL.createObjectURL(file) }];
    }
  }

  function removeAttachment(index: number) {
    URL.revokeObjectURL(attachments[index].preview);
    attachments = attachments.filter((_, i) => i !== index);
  }

  // Cleanup ObjectURLs on component unmount
  $effect(() => {
    return () => {
      for (const a of attachments) URL.revokeObjectURL(a.preview);
    };
  });

  function autoResize() {
    if (!textareaEl) return;
    textareaEl.style.height = 'auto';
    const minH = 60; // ~3 lines
    textareaEl.style.height = Math.max(minH, Math.min(textareaEl.scrollHeight, 135)) + 'px';
  }

  // The viewed session determines whether the input is enabled
  let viewedSession = $derived(sessions.find(s => s.id === sessionId) ?? null);
  let inputEnabled = $derived(
    viewedSession?.status === 'input-required' || viewedSession?.status === 'working'
  );
  let canSend = $derived(inputEnabled && (messageText.trim().length > 0 || attachments.length > 0) && !sending);

  async function handleSend() {
    if (!sessionId || !canSend) return;
    sending = true;
    sendError = null;
    try {
      const parts: import('../types').MessagePart[] = [];
      const text = messageText.trim();
      if (text) {
        parts.push({ kind: 'text', text });
      }
      for (const att of attachments) {
        const bytes = await fileToBase64(att.file);
        parts.push({ kind: 'file', file: { name: att.file.name, mimeType: att.file.type, bytes } });
      }
      await api.postMessage(sessionId, parts);
      messageText = '';
      for (const a of attachments) URL.revokeObjectURL(a.preview);
      attachments = [];
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

  function handlePaste(e: ClipboardEvent) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) addFiles([file]);
        return;
      }
    }
  }

  let dragOver = $state(false);

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    dragOver = true;
  }

  function handleDragLeave() {
    dragOver = false;
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    if (e.dataTransfer?.files) {
      addFiles(e.dataTransfer.files);
    }
  }

  // Close plus menu on outside click
  function handleDocClick(e: MouseEvent) {
    const target = e.target as HTMLElement;
    if (plusMenuOpen && !target?.closest('.plus-btn') && !target?.closest('.plus-menu')) {
      plusMenuOpen = false;
    }
  }

  $effect(() => {
    if (plusMenuOpen) {
      document.addEventListener('click', handleDocClick, true);
      return () => document.removeEventListener('click', handleDocClick, true);
    }
  });

  $effect(() => {
    messageText; // track dependency
    autoResize();
  });

  function handleScroll() {
    if (!scrollContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  $effect(() => {
    const _len = events.length; // dependency: triggers on every new event
    const _eph = ephemeralText; // dependency: triggers on ephemeral streaming updates
    const _ephThink = ephemeralThinking; // dependency: triggers on ephemeral thinking updates
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

  interface ToolGroupEntry {
    call: NormalizedToolCall;
    result?: NormalizedToolResult;
    time: string;
  }

  type ChatEntry =
    | { type: 'agent'; text: string; agentLabel: string; time: string; key: string; isStreaming: boolean }
    | { type: 'user'; text: string; time: string; key: string }
    | { type: 'lateral'; senderName: string; text: string; time: string; key: string }
    | { type: 'state'; text: string; time: string; key: string }
    | { type: 'tool'; icon: string; text: string; time: string; key: string }
    | { type: 'tool_group'; group: ToolGroupEntry; key: string }
    | { type: 'tool_stream'; groups: ToolGroupEntry[]; live: boolean; key: string }
    | { type: 'thinking'; data: NormalizedThinking; time: string; key: string; isStreaming?: boolean; startedAt?: string; durationMs?: number }
    | { type: 'data_fallback'; data: Record<string, unknown>; time: string; key: string }
    | { type: 'error'; message: string; stderr?: string; time: string; key: string }
    | { type: 'fyi'; text: string; time: string; key: string }
    | { type: 'child_response'; agentLabel: string; text: string; time: string; key: string }
    | { type: 'todo_write'; todos: TodoItem[]; time: string; key: string }
    | { type: 'user_image'; mimeType: string; bytes: string; name?: string; time: string; key: string }
    | { type: 'lateral_image'; senderName: string; mimeType: string; bytes: string; name?: string; time: string; key: string };

  function resolveAgentType(sessionId: string | null): AgentType {
    const session = sessions.find(s => s.id === sessionId);
    const agent = agents.find(a => a.id === session?.agent_id);
    return agent?.agent_type ?? 'acp';
  }

  function parseEntries(evs: Event[]): ChatEntry[] {
    const entries: ChatEntry[] = [];
    const toolGroups = new Map<string, ToolGroupEntry>();
    const agentLabel = leadSession ? agentName(leadSession.agent_id) : 'Agent';
    let seq = 0;
    let lastAgentSessionId: string | null = null;

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

        // Pre-scan for sender metadata (inter-agent message)
        const senderPart = msg.parts.find(
          p => p.kind === 'data' && (p.data as Record<string, unknown>)?.type === 'sender'
        );
        const senderName = senderPart
          ? ((senderPart as { kind: 'data'; data: Record<string, unknown> }).data.name as string) || 'unknown'
          : null;

        for (const part of msg.parts) {
          if (part.kind === 'data') {
            const d = part.data as Record<string, unknown>;

            // Skip sender metadata part — handled via pre-scan above
            if (d.type === 'sender') continue;

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
            if (isTurnCompleteData(d as unknown as import('../types').DataPartPayload)) {
              const tc = d as unknown as import('../types').TurnCompleteData;
              const childSession = sessions.find(s => s.id === tc.child_session_id);
              const childAgentLabel = childSession ? agentName(childSession.agent_id) : 'Child';
              entries.push({ type: 'child_response', agentLabel: childAgentLabel, text: tc.message, time, key: `${ev.id}-${seq++}` });
              continue;
            }

            // Normalize SDK/ACP data parts
            const norm = normalizeDataPart(agentType, d);
            switch (norm.normalized) {
              case 'tool_call': {
                // TodoWrite → emit as todo_write entry (not generic tool_group)
                if (norm.title === 'TodoWrite' && norm.input && typeof norm.input === 'object') {
                  const input = norm.input as { todos?: unknown[] };
                  if (Array.isArray(input.todos)) {
                    const todos: TodoItem[] = input.todos.filter((t: any) => t != null && typeof t === 'object').map((t: any) => ({
                      content: String(t.content ?? ''),
                      status: (['pending', 'in_progress', 'completed'].includes(t.status) ? t.status : 'pending') as TodoItem['status'],
                    }));
                    entries.push({ type: 'todo_write', todos, time, key: `${ev.id}-${seq++}` });
                    // Register in toolGroups so tool_result merges silently (no orphan entry)
                    if (norm.toolCallId) {
                      toolGroups.set(norm.toolCallId, { call: norm, time });
                    }
                    break;
                  }
                }
                if (norm.toolCallId) {
                  const existing = toolGroups.get(norm.toolCallId);
                  if (existing) {
                    // tool_call_update — patch-merge, preserve existing values for absent fields
                    existing.call = {
                      ...existing.call,
                      ...(norm.title ? { title: norm.title } : {}),
                      ...(norm.content ? { content: norm.content } : {}),
                      ...(norm.input !== undefined ? { input: norm.input } : {}),
                      ...(norm.kind ? { kind: norm.kind } : {}),
                      status: norm.status ?? existing.call.status,
                    };
                    break;
                  }
                  const group: ToolGroupEntry = { call: norm, time };
                  toolGroups.set(norm.toolCallId, group);
                  entries.push({ type: 'tool_group', group, key: `${ev.id}-${seq++}` });
                } else {
                  // No toolCallId — ungroupable, standalone group
                  entries.push({ type: 'tool_group', group: { call: norm, time }, key: `${ev.id}-${seq++}` });
                }
                break;
              }
              case 'tool_result': {
                if (norm.toolCallId) {
                  const existing = toolGroups.get(norm.toolCallId);
                  if (existing) {
                    // Merge result into existing group — no new entry
                    existing.result = norm;
                    break;
                  }
                }
                // Orphan result — standalone group with synthetic call
                const group: ToolGroupEntry = {
                  call: { normalized: 'tool_call', toolCallId: norm.toolCallId ?? '', title: norm.isError ? 'Error' : 'Result' },
                  result: norm,
                  time,
                };
                entries.push({ type: 'tool_group', group, key: `${ev.id}-${seq++}` });
                break;
              }
              case 'thinking': {
                const prevEntry = entries.length > 0 ? entries[entries.length - 1] : null;
                if (prevEntry && prevEntry.type === 'thinking') {
                  (prevEntry as { data: NormalizedThinking }).data = {
                    ...prevEntry.data,
                    text: prevEntry.data.text + norm.text,
                  };
                } else {
                  entries.push({ type: 'thinking', data: norm, time, key: `${ev.id}-${seq++}` });
                }
                break;
              }
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
            const fp = (part as { kind: 'file'; file: { name?: string; mimeType?: string; bytes?: string } }).file;
            if (fp?.bytes && fp?.mimeType?.startsWith('image/') && senderName) {
              entries.push({ type: 'lateral_image', senderName, mimeType: fp.mimeType, bytes: fp.bytes, name: fp.name, time, key: `${ev.id}-${seq++}` });
            } else if (msg.role === 'user' && fp?.bytes && fp?.mimeType?.startsWith('image/')) {
              entries.push({ type: 'user_image', mimeType: fp.mimeType, bytes: fp.bytes, name: fp.name, time, key: `${ev.id}-${seq++}` });
            } else {
              const name = fp?.name ?? 'file';
              entries.push({ type: 'tool', icon: '\u25A1', text: `[file] ${name}`, time, key: `${ev.id}-${seq++}` });
            }
          } else if (part.kind === 'text') {
            const text = part.text as string;
            if (senderName) {
              entries.push({ type: 'lateral', senderName, text, time, key: `${ev.id}-${seq++}` });
            } else if (msg.role === 'user') {
              entries.push({ type: 'user', text, time, key: `${ev.id}-${seq++}` });
            } else {
              const prevEntry = entries.length > 0 ? entries[entries.length - 1] : null;
              if (prevEntry && prevEntry.type === 'agent' && prevEntry.agentLabel === agentLabel && ev.session_id === lastAgentSessionId) {
                prevEntry.text += text;
              } else {
                entries.push({ type: 'agent', text, agentLabel, time, key: `${ev.id}-${seq++}`, isStreaming: false });
                lastAgentSessionId = ev.session_id ?? null;
              }
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

  function groupToolStreams(entries: ChatEntry[]): ChatEntry[] {
    const result: ChatEntry[] = [];
    let i = 0;
    while (i < entries.length) {
      if (entries[i].type === 'tool_group') {
        const runStart = i;
        while (i < entries.length && entries[i].type === 'tool_group') i++;
        const runLen = i - runStart;
        if (runLen >= 3) {
          const groups: ToolGroupEntry[] = [];
          for (let j = runStart; j < i; j++) {
            groups.push((entries[j] as { type: 'tool_group'; group: ToolGroupEntry; key: string }).group);
          }
          const isTrailing = i === entries.length;
          const hasPending = groups.some(g =>
            g.call.status !== 'completed' && g.call.status !== 'failed' && g.result == null
          );
          result.push({
            type: 'tool_stream',
            groups,
            live: isTrailing && hasPending,
            key: `stream-${(entries[runStart] as { key: string }).key}`,
          });
        } else {
          for (let j = runStart; j < i; j++) result.push(entries[j]);
        }
      } else {
        result.push(entries[i]);
        i++;
      }
    }
    return result;
  }

  let sessionIsActive = $derived(viewedSession?.status === 'working');

  // Capture the time once when ephemeral streaming starts (avoids recomputing on every tick)
  let ephemeralStartTime = $state('');
  $effect.pre(() => {
    if (ephemeralText && !ephemeralStartTime) {
      ephemeralStartTime = formatTime(new Date().toISOString());
    } else if (!ephemeralText) {
      ephemeralStartTime = '';
    }
  });


  let parsed = $derived.by(() => {
    const entries = groupToolStreams(parseEntries(events));

    // Ephemeral thinking: show as streaming thinking entry
    if (ephemeralThinking?.text) {
      entries.push({
        type: 'thinking',
        data: { normalized: 'thinking', text: ephemeralThinking.text },
        time: formatTime(ephemeralThinking.startedAt),
        key: 'ephemeral-thinking',
        isStreaming: true,
        startedAt: ephemeralThinking.startedAt,
      });
    }

    if (ephemeralText) {
      const agentLabel = leadSession ? agentName(leadSession.agent_id) : 'Agent';
      entries.push({
        type: 'agent',
        text: ephemeralText,
        agentLabel,
        time: ephemeralStartTime || formatTime(new Date().toISOString()),
        key: 'ephemeral-stream',
        isStreaming: true,
      });
    } else if (sessionIsActive && !ephemeralThinking?.text) {
      for (let i = entries.length - 1; i >= 0; i--) {
        const entry = entries[i];
        if (entry.type === 'agent') {
          entries[i] = { ...entry, isStreaming: true };
          break;
        }
      }
    }

    // Apply settled duration (computed by ExecutionDetail when ephemeral buffer clears)
    if (settledThinkingDuration && settledThinkingDuration.durationMs > 0 && !ephemeralThinking?.text) {
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.type === 'thinking' && !e.isStreaming) {
          entries[i] = { ...e, durationMs: settledThinkingDuration.durationMs, startedAt: settledThinkingDuration.startedAt };
          break;
        }
      }
    }

    return entries;
  });

  let latestTodos = $derived.by((): TodoItem[] => {
    for (let i = parsed.length - 1; i >= 0; i--) {
      if (parsed[i].type === 'todo_write') {
        return (parsed[i] as { type: 'todo_write'; todos: TodoItem[] }).todos;
      }
    }
    return [];
  });

  let filteredParsed = $derived(
    eventFilter === 'all' ? parsed
      : parsed.filter(entry => EVENT_FILTER_GROUPS[eventFilter]?.has(entry.type) ?? false)
  );
</script>

<div class="chat-container">
<div class="event-filter-pills" role="radiogroup" aria-label="Filter events">
  {#each EVENT_FILTER_PILLS as pill}
    <button
      class="event-filter-pill"
      class:active={eventFilter === pill.value}
      role="radio"
      aria-checked={eventFilter === pill.value}
      onclick={() => onfilterchange?.(pill.value)}
    >{pill.label}</button>
  {/each}
</div>
<div class="chat-scroll scroll-thin" bind:this={scrollContainer} onscroll={handleScroll}>
  {#if filteredParsed.length === 0}
    <div class="chat-empty">{parsed.length === 0 ? 'No messages yet' : 'No matching events'}</div>
  {:else}
    <div class="chat-messages">
      {#each filteredParsed as entry (entry.key)}
        {#if entry.type === 'agent'}
          <div class="chat-row agent-row">
            <div class="agent-prose">
              <div class="agent-prose-header">{entry.agentLabel}</div>
              <div class="agent-prose-body"><Markdown text={entry.text} streaming={entry.isStreaming} /></div>
              <div class="agent-prose-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'user'}
          <div class="chat-row user-row">
            <div class="bubble user-bubble">
              <div class="bubble-text">{entry.text}</div>
              <div class="bubble-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'user_image'}
          <div class="chat-row user-row">
            <div class="bubble user-bubble user-image-bubble">
              <img src="data:{entry.mimeType};base64,{entry.bytes}" alt={entry.name ?? 'Attached image'} class="user-image" />
              <div class="bubble-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'lateral'}
          <div class="chat-row lateral-row">
            <div class="lateral-message">
              <div class="lateral-header">From {entry.senderName}</div>
              <div class="lateral-body"><Markdown text={entry.text} /></div>
              <div class="lateral-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'lateral_image'}
          <div class="chat-row lateral-row">
            <div class="lateral-message">
              <div class="lateral-header">From {entry.senderName}</div>
              <div class="lateral-body">
                <img src="data:{entry.mimeType};base64,{entry.bytes}" alt={entry.name ?? 'Attached image'} class="user-image" />
              </div>
              <div class="lateral-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'child_response'}
          <div class="chat-row child-response-row">
            <div class="child-response">
              <div class="child-response-header">{entry.agentLabel}</div>
              <div class="child-response-body"><Markdown text={entry.text} /></div>
              <div class="child-response-time">{entry.time}</div>
            </div>
          </div>
        {:else if entry.type === 'state'}
          <div class="chat-row state-row">
            <span class="state-text">{entry.text}</span>
          </div>
        {:else if entry.type === 'tool_group'}
          <div class="chat-row tool-row">
            <ToolGroup call={entry.group.call} result={entry.group.result} />
          </div>
        {:else if entry.type === 'tool_stream'}
          <div class="chat-row tool-row">
            <ToolStream groups={entry.groups} live={entry.live} />
          </div>
        {:else if entry.type === 'todo_write' && entry.todos.length > 0}
          <div class="chat-row tool-row">
            <TodoChecklist todos={entry.todos} />
          </div>
        {:else if entry.type === 'thinking'}
          <div class="chat-row tool-row">
            <ThinkingBlock data={entry.data} isStreaming={entry.isStreaming ?? false} durationMs={entry.durationMs ?? 0} />
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

{#if latestTodos.length > 0}
  <TodoPanel todos={latestTodos} />
{/if}

{#if !shouldAutoScroll}
  <button
    class="scroll-to-bottom"
    class:has-panel={latestTodos.length > 0}
    onclick={() => {
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
        shouldAutoScroll = true;
      }
    }}
    aria-label="Scroll to bottom"
  >{'\u2193'}</button>
{/if}

<div class="chat-input-area">
  {#if sendError}
    <div class="send-error">{sendError}</div>
  {/if}
  {#if plusMenuOpen}
    <div class="plus-menu" role="menu">
      <button class="plus-menu-item" class:enabled={true} role="menuitem" onclick={() => { fileInputEl.click(); plusMenuOpen = false; }}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M14 10l-3.5-3.5L7 10M14 13H2V3h12v10z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="5" cy="6" r="1.25" stroke="currentColor" stroke-width="1.3"/>
        </svg>
        Upload image
      </button>
      <button class="plus-menu-item" role="menuitem" disabled title="Coming soon">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M9 2H4a1 1 0 00-1 1v10a1 1 0 001 1h8a1 1 0 001-1V6L9 2z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M9 2v4h4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Attach file
      </button>
    </div>
  {/if}
  <div class="chat-input-card" class:drag-over={dragOver} ondragover={handleDragOver} ondragleave={handleDragLeave} ondrop={handleDrop}>
    <textarea
      class="chat-input"
      aria-label="Message to agent"
      placeholder={viewedSession?.status === 'input-required' ? 'Type a message...' : viewedSession?.status === 'working' ? 'Message will be delivered after current step...' : 'Agent is working...'}
      disabled={!inputEnabled || sending}
      bind:value={messageText}
      bind:this={textareaEl}
      onkeydown={handleKeydown}
      onpaste={handlePaste}
      oninput={autoResize}
      rows="3"
    ></textarea>
    {#if attachments.length > 0}
      <div class="attachment-strip">
        {#each attachments as att, i}
          <div class="attachment-thumb">
            <img src={att.preview} alt={att.file.name} />
            <button class="attachment-remove" onclick={() => removeAttachment(i)} aria-label="Remove attachment">×</button>
          </div>
        {/each}
      </div>
    {/if}
    <div class="chat-input-toolbar">
      <div class="toolbar-left">
        <button
          class="toolbar-btn plus-btn"
          aria-label="Attach content"
          aria-expanded={plusMenuOpen}
          onclick={() => { plusMenuOpen = !plusMenuOpen; }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
      <button
        class="send-btn"
        disabled={!canSend}
        onclick={handleSend}
        aria-label="Send message"
      >
        {#if sending}
          <span class="sending-dots">...</span>
        {:else}
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M14 2L7 9M14 2l-4 12-3-5-5-3 12-4z" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        {/if}
      </button>
    </div>
  </div>
</div>
  <input
    type="file"
    accept="image/jpeg,image/png,image/webp,image/gif"
    multiple
    class="sr-only"
    bind:this={fileInputEl}
    onchange={(e) => {
      const target = e.currentTarget as HTMLInputElement;
      if (target.files) addFiles(target.files);
      target.value = '';
    }}
  />
</div>

<style>
  .event-filter-pills {
    display: flex;
    gap: 2px;
    padding: 0.375rem 1rem;
    flex-shrink: 0;
  }
  .event-filter-pill {
    padding: 0.1875rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.1s, color 0.1s, border-color 0.1s;
  }
  .event-filter-pill:hover:not(.active) {
    background: hsl(var(--muted) / 0.5);
  }
  .event-filter-pill.active {
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    border-color: hsl(var(--primary) / 0.3);
    font-weight: 600;
  }

  .chat-container {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
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
    justify-content: flex-start;
  }

  /* Agent messages: document flow (no bubble, no background) */
  .agent-prose {
    width: 100%;
    padding: 0.25rem 0;
    font-size: 0.8125rem;
    line-height: 1.5;
    color: hsl(var(--foreground));
    word-break: break-word;
  }

  .agent-prose-header {
    font-size: 0.6875rem;
    font-weight: 600;
    color: hsl(var(--primary));
    margin-bottom: 0.125rem;
  }

  .agent-prose-time {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
    font-family: var(--font-mono);
  }

  /* User messages: keep bubble layout */
  .bubble {
    max-width: 75%;
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-lg);
    font-size: 0.8125rem;
    line-height: 1.5;
    word-break: break-word;
  }

  .user-bubble {
    background: hsl(var(--primary) / 0.15);
    color: hsl(var(--foreground));
    border-bottom-right-radius: var(--radius-sm);
  }

  .user-bubble .bubble-text {
    white-space: pre-wrap;
  }

  .lateral-row {
    justify-content: flex-start;
  }

  .lateral-message {
    max-width: 85%;
    padding: 0.375rem 0.75rem;
    border-left: 2px solid hsl(var(--status-working));
    background: hsl(var(--muted) / 0.15);
    border-radius: 0 var(--radius) var(--radius) 0;
    font-size: 0.8125rem;
    line-height: 1.5;
    word-break: break-word;
  }

  .lateral-header {
    font-size: 0.6875rem;
    font-weight: 600;
    color: hsl(var(--status-working));
    margin-bottom: 0.125rem;
  }

  .lateral-body {
    color: hsl(var(--foreground));
  }

  .lateral-time {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
    font-family: var(--font-mono);
  }

  .child-response-row {
    justify-content: flex-start;
  }

  .child-response {
    max-width: 85%;
    padding: 0.375rem 0.75rem;
    border-left: 2px solid hsl(var(--status-success));
    background: hsl(var(--muted) / 0.15);
    border-radius: 0 var(--radius) var(--radius) 0;
    font-size: 0.8125rem;
    line-height: 1.5;
    word-break: break-word;
  }

  .child-response-header {
    font-size: 0.6875rem;
    font-weight: 600;
    color: hsl(var(--status-success));
    margin-bottom: 0.125rem;
  }

  .child-response-body :global(.markdown-body) {
    font-size: inherit;
    line-height: inherit;
  }

  .child-response-time {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
    font-family: var(--font-mono);
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

  /* Platform event cards (delegate, escalate, plan) */
  .tool-card {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 0.625rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.3);
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    max-width: 85%;
  }

  .tool-icon {
    flex-shrink: 0;
    font-size: 0.6875rem;
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
    border-radius: var(--radius);
    background: hsl(var(--status-working) / 0.08);
    border: 1px solid hsl(var(--status-working) / 0.2);
    font-size: 0.6875rem;
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

  /* Scroll-to-bottom FAB */
  .scroll-to-bottom {
    position: absolute;
    bottom: 4rem;
    right: 1.5rem;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--card) / 0.9);
    color: hsl(var(--foreground));
    font-size: 1rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 4px hsl(var(--shadow-hsl) / 0.15);
    z-index: 10;
    transition: opacity 0.15s;
  }

  .scroll-to-bottom.has-panel {
    bottom: 7rem;
  }

  .scroll-to-bottom:hover {
    background: hsl(var(--card));
    border-color: hsl(var(--primary));
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
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.6875rem;
  }

  .chat-input-card {
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-lg);
    background: hsl(var(--background));
    transition: border-color 0.15s;
  }

  .chat-input-card:focus-within {
    border-color: hsl(var(--primary));
  }

  .chat-input {
    display: block;
    width: 100%;
    padding: 0.625rem 0.75rem 0.375rem;
    border: none;
    background: transparent;
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: inherit;
    line-height: 1.5;
    min-height: 60px;
    max-height: 135px;
    overflow-y: auto;
    resize: none;
    outline: none;
    transition: height 0.1s ease;
  }

  .chat-input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .chat-input::placeholder {
    color: hsl(var(--muted-foreground));
  }

  .chat-input-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.25rem 0.375rem;
    border-top: 1px solid hsl(var(--border) / 0.5);
  }

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 0.125rem;
  }

  .toolbar-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }

  .toolbar-btn:hover {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
  }

  .plus-menu {
    padding: 0.25rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    box-shadow: 0 4px 12px hsl(var(--shadow-hsl) / 0.2);
    display: inline-flex;
    flex-direction: column;
    gap: 0.0625rem;
    margin-bottom: 0.375rem;
  }

  .plus-menu-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.75rem;
    cursor: default;
    opacity: 0.5;
    text-align: left;
    white-space: nowrap;
  }

  .plus-menu-item svg {
    flex-shrink: 0;
  }

  .send-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 1.75rem;
    border-radius: var(--radius-sm);
    border: none;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    cursor: pointer;
    transition: opacity 0.15s, filter 0.15s;
    flex-shrink: 0;
  }

  .send-btn:hover:not(:disabled) {
    filter: brightness(1.1);
  }

  .send-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .sending-dots {
    font-size: 0.75rem;
    letter-spacing: 0.05em;
  }

  .attachment-strip {
    display: flex;
    gap: 0.5rem;
    padding: 0.375rem 0.75rem;
    overflow-x: auto;
  }

  .attachment-thumb {
    position: relative;
    flex-shrink: 0;
    width: 3.5rem;
    height: 3.5rem;
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid hsl(var(--border));
  }

  .attachment-thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .attachment-remove {
    position: absolute;
    top: 0;
    right: 0;
    width: 1.125rem;
    height: 1.125rem;
    border-radius: 0 0 0 var(--radius-sm);
    border: none;
    background: hsl(var(--foreground) / 0.7);
    color: hsl(var(--background));
    font-size: 0.75rem;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .attachment-remove:hover {
    background: hsl(var(--status-danger));
  }

  .drag-over {
    border-color: hsl(var(--primary)) !important;
    background: hsl(var(--primary) / 0.05) !important;
  }

  .plus-menu-item.enabled {
    opacity: 1;
    cursor: pointer;
    color: hsl(var(--foreground));
  }

  .plus-menu-item.enabled:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border-width: 0;
  }

  .user-image-bubble {
    padding: 0.25rem;
  }

  .user-image {
    max-width: 20rem;
    max-height: 15rem;
    border-radius: calc(var(--radius-lg) - 2px);
    display: block;
  }
</style>
