export type EventFilter = 'all' | 'messages' | 'tools' | 'errors' | 'status';

export const EVENT_FILTER_GROUPS: Record<Exclude<EventFilter, 'all'>, Set<string>> = {
  // 'compaction' is in both messages and status: it renders as an inline divider
  // in the chat flow (messages) and represents a system status event (status).
  messages: new Set(['agent', 'user', 'lateral', 'thinking', 'child_response', 'compaction']),
  tools:    new Set(['tool_group', 'tool_stream', 'todo_write']),
  errors:   new Set(['error']),
  status:   new Set(['state', 'tool', 'fyi', 'compaction']),
};

export const EVENT_FILTER_PILLS: ReadonlyArray<{ value: EventFilter; label: string }> = [
  { value: 'all',      label: 'All' },
  { value: 'messages', label: 'Messages' },
  { value: 'tools',    label: 'Tools' },
  { value: 'errors',   label: 'Errors' },
  { value: 'status',   label: 'Status' },
];
