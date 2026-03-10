export type EventFilter = 'all' | 'messages' | 'tools' | 'errors' | 'status';

export const EVENT_FILTER_GROUPS: Record<Exclude<EventFilter, 'all'>, Set<string>> = {
  messages: new Set(['agent', 'user', 'lateral', 'thinking']),
  tools:    new Set(['tool_group', 'tool_stream']),
  errors:   new Set(['error']),
  status:   new Set(['state', 'tool', 'fyi']),
};

export const EVENT_FILTER_PILLS: ReadonlyArray<{ value: EventFilter; label: string }> = [
  { value: 'all',      label: 'All' },
  { value: 'messages', label: 'Messages' },
  { value: 'tools',    label: 'Tools' },
  { value: 'errors',   label: 'Errors' },
  { value: 'status',   label: 'Status' },
];
