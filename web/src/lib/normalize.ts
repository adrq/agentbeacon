// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import type {
  AgentType,
  NormalizedText,
  NormalizedUsage,
  NormalizedError,
  NormalizedFyi,
  NormalizedDebug,
} from './types';

export interface NormalizedToolCall {
  normalized: 'tool_call';
  toolCallId: string;
  /** Short verb: "Bash", "Read", "Edit". Always present — mappings synthesize one. */
  name: string;
  /** MCP origin, rendered as a dim prefix before the name. */
  server?: string;
  /** One-line "what": the file, the command, the query. May be empty. */
  subject: string;
  status?: string;
  input?: unknown;
  kind?: string;
  content?: unknown[];
  /**
   * Result text, when the source event is self-contained (Codex `item/completed`
   * carries its own output). Claude splits tool_use/tool_result across two data
   * parts, which `normalizeDataPart` cannot pair because it is stateless — that
   * merge happens in ChatView. Renderers resolve `output ?? result.content`.
   */
  output?: string;
  exitCode?: number;
  /**
   * A before/after pair the renderer can diff for a `+N -M` line stat.
   *
   * Deliberately NOT diffed here: `normalizeDataPart` runs for every data part on
   * every transcript reparse, whereas the renderer computes the stat in a
   * `$derived` that — under virtualization — only runs for rows actually on
   * screen. An empty `before` means a pure addition and skips diffing entirely.
   */
  diffPair?: { before: string; after: string };
  /** `output` is already a unified diff and renders colorized. */
  outputIsDiff?: boolean;
}

/** Argument keys that identify what a call acted on, in priority order. */
const SUBJECT_KEYS = [
  'command', 'file_path', 'path', 'pattern', 'query', 'url', 'agent', 'skill', 'prompt',
] as const;
const BASENAME_KEYS = new Set(['file_path', 'path']);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Collapse any whitespace run to a single space so a subject is always one line. */
function oneLine(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

/** Host of a URL, for subjects where the full URL would swamp the row. */
function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function basename(p: string): string {
  const trimmed = p.replace(/\/+$/, '');
  return trimmed.slice(trimmed.lastIndexOf('/') + 1) || trimmed;
}

/**
 * The one-line "what" for a call, from its arguments.
 *
 * A named key wins when present, which is the common case. Otherwise fall back to compact
 * `key: value` pairs of scalar args only — long strings would swamp the row, and
 * a bare UUID leads with an identifier, which reads as noise in a one-line row.
 */
export function deriveSubject(input: unknown): string {
  if (input == null) return '';
  if (typeof input !== 'object') return oneLine(String(input));

  const obj = input as Record<string, unknown>;
  for (const key of SUBJECT_KEYS) {
    const value = obj[key];
    if (typeof value === 'string' && value !== '') {
      return oneLine(BASENAME_KEYS.has(key) ? basename(value) : value);
    }
  }

  const parts: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (value == null) continue;
    if (typeof value === 'object') continue;
    let text = String(value);
    if (UUID_RE.test(text)) text = `${text.slice(0, 8)}…`;
    else if (text.length > 40) continue;
    parts.push(`${key}: ${text}`);
    if (parts.length === 2) break;
  }
  return oneLine(parts.join(', '));
}

/**
 * The before/after strings a call changed, when its arguments describe an edit.
 *
 * Keyed on the argument shape rather than the tool name: `old_string`/`new_string`
 * is the actual signal, and it holds for `copilot_sdk` too, which shares Claude's
 * tool vocabulary. A `content` write with no prior text is a pure addition.
 */
function deriveDiffPair(input: unknown): { before: string; after: string } | undefined {
  if (input == null || typeof input !== 'object' || Array.isArray(input)) return undefined;
  const obj = input as Record<string, unknown>;
  const before = obj.old_string;
  const after = obj.new_string;
  if (typeof before === 'string' && typeof after === 'string') return { before, after };
  if (typeof obj.content === 'string' && typeof obj.file_path === 'string') {
    return { before: '', after: obj.content };
  }
  return undefined;
}

/** `mcp__server__tool` → `{ server, name }`. Plain names pass through unchanged. */
export function splitToolName(raw: string): { name: string; server?: string } {
  if (!raw.startsWith('mcp__')) return { name: raw };
  const rest = raw.slice('mcp__'.length);
  const sep = rest.indexOf('__');
  if (sep === -1) return { name: rest };
  return { server: rest.slice(0, sep), name: rest.slice(sep + 2) };
}

/**
 * Codex wraps essentially every command as `/bin/bash -lc "…"`; that envelope is noise.
 *
 * The outer delimiters are stripped independently rather than as a matched pair:
 * shell quote-juggling such as `--data-urlencode '"'q=x'"'` legitimately opens
 * with `'` and closes with `"`, and an unquoted body (`/bin/bash -lc true`) has
 * neither. Requiring a matched pair left both forms showing the raw wrapper.
 */
export function stripShellWrapper(command: string): string {
  const match = /^\/bin\/bash\s+-lc\s+([\s\S]*)$/.exec(command);
  if (!match) return command;
  let body = match[1].trim();
  if (body.length > 1 && (body[0] === "'" || body[0] === '"')) body = body.slice(1);
  const last = body[body.length - 1];
  if (body.length > 0 && (last === "'" || last === '"')) body = body.slice(0, -1);
  return body;
}

/**
 * A Codex file change as unified-diff text.
 *
 * Only `kind.type === 'update'` actually ships a unified diff; `add` and `delete`
 * carry the raw file content with no prefixes — and they are the majority.
 * Treating those as a diff both zeroed the +/- stat and
 * risked mis-colouring any file whose lines begin with `-` or `+`, e.g. markdown
 * bullets or YAML lists. Prefixing them is what a real diff shows for a whole
 * added or deleted file.
 */
function changeToUnified(change: Record<string, unknown>): string {
  const diff = typeof change?.diff === 'string' ? change.diff : '';
  if (!diff) return '';
  const kind = (change.kind as Record<string, unknown> | undefined)?.type;
  if (kind === 'update') return diff;
  const sign = kind === 'delete' ? '-' : '+';
  return diff.replace(/\n$/, '').split('\n').map(line => sign + line).join('\n');
}

/** Codex item.status → the renderer's three states. */
function codexStatus(status: unknown): string {
  if (status === 'inProgress') return 'running';
  if (status === 'completed') return 'completed';
  return 'failed';
}

function asText(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (value == null) return undefined;
  return JSON.stringify(value, null, 2);
}

export interface NormalizedToolResult {
  normalized: 'tool_result';
  toolCallId: string;
  content?: string | unknown[];
  isError?: boolean;
}

export interface NormalizedThinking {
  normalized: 'thinking';
  text: string;
}

/**
 * A context compaction, from either executor.
 *
 * Claude sends `{type: 'compaction'}`; Codex sends a `contextCompaction` item
 * (and, on older data, the deprecated `contextCompacted` notification the worker
 * rewrites to Claude's shape). Previously each consumer sniffed the raw Claude
 * shape via `isCompactionData`, in three separate places, so Codex compactions
 * were invisible and uncounted.
 */
export interface NormalizedCompaction {
  normalized: 'compaction';
  trigger?: string;
}

export type NormalizedData =
  | NormalizedToolCall
  | NormalizedToolResult
  | NormalizedThinking
  | NormalizedCompaction
  | NormalizedUsage
  | NormalizedText
  | NormalizedError
  | NormalizedFyi
  | NormalizedDebug
  | { normalized: 'unknown'; raw: Record<string, unknown> };

export function normalizeDataPart(agentType: AgentType, raw: Record<string, unknown>): NormalizedData {
  const type = raw.type as string | undefined;

  // Platform events handled separately in ChatView
  if (type === 'escalate' || type === 'delegate' || type === 'turn_complete' || type === 'sender')
    return { normalized: 'unknown', raw };

  switch (agentType) {
    case 'claude_sdk':
    case 'copilot_sdk':
      return normalizeSdkPart(raw);
    case 'codex_sdk':
      return normalizeCodexPart(raw);
    case 'acp':
      return normalizeAcpPart(raw);
    default:
      return { normalized: 'unknown', raw };
  }
}

function normalizeSdkPart(raw: Record<string, unknown>): NormalizedData {
  switch (raw.type) {
    case 'tool_use': {
      const { name, server } = splitToolName((raw.name as string) ?? '');
      return {
        normalized: 'tool_call',
        toolCallId: (raw.id as string) ?? '',
        name,
        server,
        subject: deriveSubject(raw.input),
        status: 'running',
        input: raw.input,
        diffPair: deriveDiffPair(raw.input),
      };
    }
    case 'tool_result':
      return {
        normalized: 'tool_result',
        toolCallId: (raw.tool_use_id as string) ?? '',
        content: raw.content as string | unknown[] | undefined,
        isError: raw.is_error as boolean | undefined,
      };
    case 'thinking':
    case 'thinking_delta':
      return {
        normalized: 'thinking',
        text: (raw.thinking as string) ?? '',
      };
    case 'compaction':
      return { normalized: 'compaction', trigger: raw.trigger as string | undefined };
    case 'usage_update':
      {
        const inputTokens = (raw.input_tokens as number) ?? 0;
        const outputTokens = (raw.output_tokens as number) ?? 0;
        return {
          normalized: 'usage',
          usedTokens: inputTokens + outputTokens,
          inputTokens,
          outputTokens,
        };
      }
    case 'usage_snapshot':
      return {
        normalized: 'usage',
        usedTokens: ((raw.input_tokens as number) ?? 0) + ((raw.output_tokens as number) ?? 0),
        inputTokens: (raw.input_tokens as number) ?? 0,
        outputTokens: (raw.output_tokens as number) ?? 0,
        modelContextWindow: (raw.context_window as number | undefined) ?? null,
      };
    default: {
      // Raw SDK assistant event with error field (e.g., auth failure, rate limit).
      // Emitted as a data part by the executor to preserve the full SDK context.
      if (raw.type === 'assistant' && typeof raw.error === 'string') {
        const msg = raw.message as Record<string, unknown> | undefined;
        const contentArr = Array.isArray(msg?.content) ? msg!.content as Array<Record<string, unknown>> : [];
        const textParts = contentArr
          .filter((c) => c.type === 'text' && typeof c.text === 'string')
          .map((c) => c.text as string);
        const humanText = textParts.join(' ') || undefined;
        return {
          normalized: 'error',
          message: raw.error as string,
          details: humanText,
        };
      }
      return { normalized: 'unknown', raw };
    }
  }
}

function normalizeCodexPart(raw: Record<string, unknown>): NormalizedData {
  // New shape: the catch-all wraps the full JSON-RPC notification as the data part,
  // so `raw` is `{method: "item/completed", params: {item: {...}}}`.
  // Backward compat: if `raw.method` is absent (old stored data), fall back to
  // the legacy `raw.type` based routing.
  const method = raw.method as string | undefined;
  const params = (raw.params ?? raw) as Record<string, unknown>;

  if (method === 'item/completed' || method === 'item/started') {
    const item = (params.item ?? params) as Record<string, unknown>;
    return normalizeCodexItem(item);
  }

  if (method === 'thread/tokenUsage/updated') {
    const usage = (params.tokenUsage ?? params.usage ?? params) as Record<string, unknown>;
    return normalizeCodexUsage(usage);
  }

  // Reasoning delta methods — extract text for thinking display
  if (method === 'item/reasoning/textDelta') {
    const rawDelta = params.delta;
    const text = typeof rawDelta === 'string' ? rawDelta : (rawDelta as any)?.text ?? '';
    return { normalized: 'thinking', text };
  }

  if (method === 'item/reasoning/summaryTextDelta') {
    const rawDelta = params.delta;
    const text = typeof rawDelta === 'string' ? rawDelta : (rawDelta as any)?.text ?? '';
    return { normalized: 'thinking', text };
  }

  // summaryPartAdded is a marker-only event (no delta field in schema)
  if (method === 'item/reasoning/summaryPartAdded') {
    return { normalized: 'thinking', text: '' };
  }

  // Output delta methods — extract text content for display
  if (method === 'item/commandExecution/outputDelta' || method === 'item/fileChange/outputDelta') {
    const delta = params.delta;
    const text = typeof delta === 'string' ? delta : (delta as any)?.text ?? '';
    return { normalized: 'text', text };
  }

  // Plan delta — show as thinking/plan text
  if (method === 'item/plan/delta') {
    const delta = params.delta;
    const text = typeof delta === 'string' ? delta : (delta as any)?.text ?? '';
    return { normalized: 'thinking', text };
  }

  // MCP tool progress — show as tool call status update
  if (method === 'mcpToolCall/progress') {
    return {
      normalized: 'tool_call',
      toolCallId: (params.id as string) ?? '',
      name: (params.tool as string) ?? 'mcp',
      server: params.server as string | undefined,
      subject: deriveSubject(params.progress),
      status: 'running',
      input: params.progress,
    };
  }

  // turn/started — lifecycle marker with no visual output.
  // Returns empty thinking (same pattern as summaryPartAdded) rather than 'unknown',
  // because ChatView's 'unknown' case falls through to DataFallback for unrecognised types.
  if (method === 'turn/started') {
    return { normalized: 'thinking', text: '' };
  }

  // Errors from the executor — surface as first-class error rows.
  if (method === 'error') {
    const err = (params.error ?? {}) as Record<string, unknown>;
    const message = (err.message as string | undefined)
      ?? (typeof err === 'string' ? err : undefined)
      ?? 'Executor error';
    const details = err.additionalDetails as string | undefined;
    return { normalized: 'error', message, details };
  }

  // Config warnings (e.g. disabled config.toml files). User-actionable but low-frequency.
  if (method === 'configWarning') {
    const summary = (params.summary as string | undefined) ?? 'Configuration warning';
    const details = params.details == null
      ? undefined
      : (typeof params.details === 'string' ? params.details : JSON.stringify(params.details, null, 2));
    return { normalized: 'fyi', title: summary, details };
  }

  // Other known-noise lifecycle methods — hidden from "All" but visible under Debug.
  // Keeping raw payload so power users can inspect if anything looks off.
  if (
    method === 'thread/status/changed' ||
    method === 'mcpServer/startupStatus/updated'
  ) {
    return { normalized: 'debug', raw, reason: method };
  }

  // Legacy / backward compat: no method field — use type-based routing (old stored data)
  if (!method) {
    const type = raw.type as string | undefined;

    // Token usage events have no `type` — detected by presence of `tokenUsage`
    if (raw.tokenUsage != null) {
      return normalizeCodexUsage(raw.tokenUsage as Record<string, unknown>);
    }

    if (type) {
      return normalizeCodexItem(raw);
    }
  }

  // Fallback for other methods / unknown shapes
  return { normalized: 'unknown', raw };
}

/** Normalize a Codex item object (from item/completed or item/started params.item). */
function normalizeCodexItem(item: Record<string, unknown>): NormalizedData {
  const type = item.type as string | undefined;

  switch (type) {
    case 'commandExecution': {
      // Codex names the shell verb nowhere, but virtually every command arrives wrapped
      // as `/bin/bash -lc "…"`. Calling it "Bash" makes Codex rows scan
      // identically to Claude's, which is the point of the whole mapping.
      const command = (item.command as string) ?? '';
      const exitCode = typeof item.exitCode === 'number' ? item.exitCode : undefined;
      const failed = item.status === 'failed' || (exitCode != null && exitCode !== 0);
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        name: 'Bash',
        subject: oneLine(stripShellWrapper(command)) || 'Shell command',
        status: item.status === 'inProgress' ? 'running' : failed ? 'failed' : 'completed',
        // The full command belongs in the expanded view; cwd is the only other
        // field worth keeping. processId/source/commandActions/pluginId are noise.
        input: { command, cwd: item.cwd },
        // Null while in progress — coerce so the field means "absent", not "empty".
        output: typeof item.aggregatedOutput === 'string' ? item.aggregatedOutput : undefined,
        exitCode,
      };
    }
    case 'fileChange': {
      const changes = Array.isArray(item.changes) ? (item.changes as Record<string, unknown>[]) : [];
      const paths = changes
        .map(c => (typeof c?.path === 'string' ? basename(c.path) : ''))
        .filter(Boolean);
      const subject = paths.length > 1 ? `${paths[0]} +${paths.length - 1}` : (paths[0] ?? '');
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        name: 'Edit',
        subject,
        status: codexStatus(item.status),
        input: { changes: item.changes },
        output: changes.map(changeToUnified).filter(Boolean).join('\n') || undefined,
        outputIsDiff: true,
      };
    }
    case 'mcpToolCall': {
      const result = item.result as Record<string, unknown> | undefined;
      const blocks = Array.isArray(result?.content) ? (result!.content as Record<string, unknown>[]) : [];
      const text = blocks
        .map(b => (typeof b?.text === 'string' ? b.text : JSON.stringify(b)))
        .join('\n');
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        name: (item.tool as string) ?? 'unknown',
        server: (item.server as string) ?? 'mcp',
        subject: deriveSubject(item.arguments),
        status: item.error != null ? 'failed' : codexStatus(item.status),
        input: item.arguments,
        content: item.result != null ? [item.result] : undefined,
        output: text || asText(item.error),
      };
    }
    case 'dynamicToolCall':
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        name: (item.tool as string) ?? 'Dynamic tool',
        subject: deriveSubject(item.arguments),
        status: codexStatus(item.status),
        input: item.arguments,
      };
    case 'reasoning': {
      // Schema: ReasoningThreadItem has summary: string[] and content: string[].
      // Prefer summary; fall back to content when summary is absent.
      // The `typeof c === 'string'` path is the schema-correct one (string[]);
      // the `c?.text` fallback handles legacy stored data with {type, text} objects.
      let reasoningText = '';
      if (Array.isArray(item.summary) && item.summary.length > 0) {
        reasoningText = (item.summary as unknown[])
          .map(c => typeof c === 'string' ? c : (c as any)?.text ?? '')
          .join('\n');
      } else if (Array.isArray(item.content)) {
        reasoningText = (item.content as unknown[])
          .map(c => typeof c === 'string' ? c : (c as any)?.text ?? '')
          .join('\n');
      }
      return {
        normalized: 'thinking',
        text: reasoningText,
      };
    }
    case 'agentMessage': {
      // Schema: AgentMessageThreadItem has `text: string` (required).
      // No content fallback — `text` is the authoritative field.
      const agentText = (item.text as string) ?? '';
      return {
        normalized: 'text',
        text: agentText,
      };
    }
    case 'contextCompaction':
    // The worker rewrites the deprecated `contextCompacted` notification into
    // Claude's `{type: 'compaction'}` shape, which lands here via the legacy
    // no-`method` branch.
    case 'compaction':
      return { normalized: 'compaction' };
    case 'webSearch': {
      // One item type covers four different actions, and they do not share a
      // field for "what happened":
      //   search      query holds the search terms
      //   openPage    query holds the URL — a fetch, not a search
      //   findInPage  action.pattern + action.url; query is a long prose compose
      //   other       query is ALWAYS empty; sometimes results, sometimes nothing
      const action = (item.action ?? {}) as Record<string, unknown>;
      const actionType = action.type;
      const url = typeof action.url === 'string' ? action.url : undefined;
      const results = Array.isArray(item.results) ? (item.results as Record<string, unknown>[]) : [];
      const lines = results.map(r => {
        const title = typeof r?.title === 'string' ? r.title : '';
        const href = typeof r?.url === 'string' ? r.url : '';
        return title && href ? `${title}\n  ${href}` : title || href;
      }).filter(Boolean);
      const output = lines.length > 0
        ? `${lines.length} result${lines.length === 1 ? '' : 's'}\n\n${lines.join('\n')}`
        : undefined;
      const call = (name: string, subject: string, input: unknown): NormalizedToolCall => ({
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        name,
        subject: oneLine(subject),
        status: 'completed',
        input,
        output,
      });

      if (actionType === 'findInPage') {
        // Searching *within* a page, not the web. The composed `query`
        // (`'pattern' in <url>`) is unusable as a subject — the URL alone runs
        // past 100 characters — so pair the pattern with just the host.
        const pattern = typeof action.pattern === 'string' ? action.pattern : '';
        const subject = pattern && url ? `${pattern} in ${hostOf(url)}` : pattern || (url ?? '');
        return call('Find', subject, { pattern, url });
      }

      const query = (item.query as string) || url || '';
      if (query) return call(actionType === 'openPage' ? 'Fetch' : 'Search', query, { query, ...(url ? { url } : {}) });

      // No query, no URL. `other` items with results are page reads and can be
      // named from the first result; the rest carry nothing that could honestly
      // fill a subject, and a bare "Search" with no object is worse than no row —
      // so they stay inspectable under Debug instead of littering the transcript.
      const firstTitle = typeof results[0]?.title === 'string' ? (results[0].title as string) : '';
      const firstUrl = typeof results[0]?.url === 'string' ? (results[0].url as string) : '';
      if (firstTitle || firstUrl) return call('Fetch', firstTitle || firstUrl, { results: item.results });
      return { normalized: 'debug', raw: item, reason: `webSearch/${String(actionType ?? 'unknown')}` };
    }
    case 'userMessage':
      // Codex echoes the user's input back via item/completed. The same text
      // already appears as a ROLE_USER message part, so suppress from "All"
      // while keeping it inspectable under Debug.
      return { normalized: 'debug', raw: item, reason: 'userMessage' };
    default:
      return { normalized: 'unknown', raw: item };
  }
}

/** Normalize a Codex token usage object. */
function normalizeCodexUsage(usage: Record<string, unknown>): NormalizedData {
  const last = (usage.last ?? {}) as Record<string, number>;
  return {
    normalized: 'usage',
    usedTokens: last.totalTokens ?? 0,
    inputTokens: last.inputTokens ?? 0,
    outputTokens: last.outputTokens ?? 0,
    modelContextWindow: (usage.modelContextWindow as number | undefined) ?? null,
  };
}

function normalizeAcpPart(raw: Record<string, unknown>): NormalizedData {
  switch (raw.type) {
    // ACP supplies a human title but no separate subject; the title is the whole
    // label, so it becomes the name and the subject stays empty.
    case 'tool_call':
      return {
        normalized: 'tool_call',
        toolCallId: (raw.toolCallId as string) ?? '',
        name: (raw.title as string) ?? '',
        subject: '',
        status: raw.status === 'in_progress' ? 'running' : (raw.status as string | undefined),
        kind: raw.kind as string | undefined,
        content: raw.content as unknown[] | undefined,
      };
    case 'tool_call_update':
      return {
        normalized: 'tool_call',
        toolCallId: (raw.toolCallId as string) ?? '',
        name: (raw.title as string) ?? '',
        subject: '',
        status: raw.status === 'in_progress' ? 'running' : (raw.status as string | undefined),
        content: raw.content as unknown[] | undefined,
      };
    case 'agent_thought_chunk':
      return {
        normalized: 'thinking',
        text: (raw.text as string) ?? '',
      };
    default:
      return { normalized: 'unknown', raw };
  }
}
