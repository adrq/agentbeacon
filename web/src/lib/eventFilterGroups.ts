export type EventFilter = 'all' | 'messages' | 'tools' | 'errors' | 'status' | 'debug';

export const EVENT_FILTER_GROUPS: Record<Exclude<EventFilter, 'all' | 'debug'>, Set<string>> = {
  // 'compaction' is in both messages and status: it renders as an inline divider
  // in the chat flow (messages) and represents a system status event (status).
  messages: new Set(['agent', 'user', 'lateral', 'thinking', 'child_response', 'compaction']),
  tools:    new Set(['tool_group', 'tool_stream', 'todo_write']),
  errors:   new Set(['error']),
  status:   new Set(['state', 'tool', 'fyi', 'compaction']),
};

// Entry types hidden from the curated "All" view but shown under "Debug".
// Keeps noisy lifecycle pings (rate limits, mcp startup, user-message echoes)
// and catch-all DataFallback rows out of the main timeline while preserving
// raw JSON inspection when things look off.
export const DEBUG_ONLY_ENTRY_TYPES: ReadonlySet<string> = new Set(['debug_event', 'data_fallback']);

export function matchesFilter(entryType: string, filter: EventFilter): boolean {
  if (filter === 'debug') return true;
  if (filter === 'all') return !DEBUG_ONLY_ENTRY_TYPES.has(entryType);
  return EVENT_FILTER_GROUPS[filter]?.has(entryType) ?? false;
}

export const EVENT_FILTER_PILLS: ReadonlyArray<{ value: EventFilter; label: string }> = [
  { value: 'all',      label: 'All' },
  { value: 'messages', label: 'Messages' },
  { value: 'tools',    label: 'Tools' },
  { value: 'errors',   label: 'Errors' },
  { value: 'status',   label: 'Status' },
  { value: 'debug',    label: 'Debug' },
];
