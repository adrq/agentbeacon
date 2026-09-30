<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { diffLines } from 'diff';
  import type { NormalizedToolCall, NormalizedToolResult } from '../../normalize';

  interface Props {
    call: NormalizedToolCall;
    result?: NormalizedToolResult;
    /** False when embedded in ToolStream, which draws its own row. */
    showRow?: boolean;
  }

  let { call, result, showRow = true }: Props = $props();

  let title = $derived(call.name || 'Unknown tool');

  // Output reaches us by two routes: Codex items are self-contained and carry
  // `call.output`; Claude splits tool_use/tool_result across two data parts that
  // ChatView pairs into `result`. Either may be present, never both.
  let output = $derived(call.output ?? (result?.content != null ? formatContent(result.content) : undefined));

  // Failure is checked first. A completed-but-nonzero-exit call carries output,
  // so an `output != null -> completed` test would silently mark real failures
  // green — observed on Codex `exit 2` and `exit 7` rows, which do produce output.
  let displayStatus = $derived(
    result?.isError === true || call.status === 'failed' || (call.exitCode != null && call.exitCode !== 0) ? 'failed' :
    result != null || call.output != null ? 'completed' :
    call.status ?? 'running'
  );
  let isError = $derived(displayStatus === 'failed');
  let isRunning = $derived(displayStatus === 'running');

  let glyph = $derived(isError ? '✗' : isRunning ? '●' : '✓');

  // Only failures earn a preview. On success it is worthless: most results are
  // too long to fit, and the ones that do fit mostly restate the subject. On a
  // failure the first line is exactly what you need.
  let errorPreview = $derived(
    isError && output ? output.replace(/\s+/g, ' ').trim().slice(0, 80) : ''
  );

  let showExitCode = $derived(call.exitCode != null && call.exitCode !== 0);

  // `+++ b/path` / `--- a/path` file headers only occur in a real unified diff,
  // which always carries @@ hunks. changeToUnified synthesises add/delete output
  // by prefixing raw file content, with no hunks and no headers — so there every
  // prefixed line is content. Testing the marker alone misreads it: a SQL
  // comment `-- note` becomes `--- note` and looks exactly like a header.
  const DIFF_HEADER = /^(\+\+\+|---) /;
  const hasHunks = (diff: string) => /^@@/m.test(diff);

  function countUnified(diff: string): { added: number; removed: number } {
    const headersPossible = hasHunks(diff);
    let added = 0, removed = 0;
    for (const line of diff.split('\n')) {
      if (headersPossible && DIFF_HEADER.test(line)) continue;
      if (line.startsWith('+')) added++;
      else if (line.startsWith('-')) removed++;
    }
    return { added, removed };
  }

  /**
   * `+N -M` line stat. Computed here rather than in the normalizer so the LCS
   * only runs for rows the virtualizer actually renders — a handful on screen,
   * not every call in the transcript on every reparse.
   */
  let stat = $derived.by(() => {
    if (call.outputIsDiff && output) return countUnified(output);
    const pair = call.diffPair;
    if (!pair) return null;
    // A pure addition needs no diff at all.
    if (pair.before === '') {
      // Drop a single trailing newline before splitting: "hello\n" is one line,
      // not two, and the empty final segment would inflate the count by one.
      const body = pair.after.replace(/\n$/, '');
      return { added: body === '' ? 0 : body.split('\n').length, removed: 0 };
    }
    let added = 0, removed = 0;
    for (const part of diffLines(pair.before, pair.after)) {
      if (part.added) added += part.count ?? 0;
      else if (part.removed) removed += part.count ?? 0;
    }
    return { added, removed };
  });

  // A failed edit changed nothing, so its would-be line stat is a lie. `diffPair`
  // is derived from the arguments at tool_use time, before the outcome is known,
  // so the suppression has to happen here.
  let showStat = $derived(!isError && stat != null && (stat.added > 0 || stat.removed > 0));

  /** Classify already-formatted unified-diff lines for colouring. */
  type DiffLine = { text: string; kind: 'add' | 'del' | 'hunk' | 'meta' | 'ctx' };
  let diffLinesOut = $derived.by((): DiffLine[] => {
    if (!call.outputIsDiff || !output) return [];
    const headersPossible = hasHunks(output);
    return output.split('\n').map((text): DiffLine => {
      if (headersPossible && DIFF_HEADER.test(text)) return { text, kind: 'meta' };
      if (text.startsWith('@@')) return { text, kind: 'hunk' };
      if (text.startsWith('\\')) return { text, kind: 'meta' };
      if (text.startsWith('+')) return { text, kind: 'add' };
      if (text.startsWith('-')) return { text, kind: 'del' };
      return { text, kind: 'ctx' };
    });
  });

  /** Longest value rendered on the same line as its key. */
  const INLINE_MAX = 80;

  interface Field {
    key: string;
    value: string;
    inline: boolean;
  }

  function stringify(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value, null, 2) ?? String(value);
    } catch {
      return String(value);
    }
  }

  /**
   * Arguments as fields, split by the shape of the value rather than by tool.
   *
   * A short scalar sits beside its key; anything longer or multi-line gets its
   * own `pre-wrap` block. JSON.stringify was escaping every newline to a literal
   * `\n`, which rendered a long file write as one unreadable ribbon. Multi-line
   * arguments are common: heredocs, and anything carrying file content or a
   * nested prompt.
   */
  function toFields(input: unknown): Field[] {
    if (input == null) return [];
    if (typeof input !== 'object') {
      const value = String(input);
      return [{ key: '', value, inline: value.length <= INLINE_MAX && !value.includes('\n') }];
    }
    if (Array.isArray(input)) {
      return [{ key: '', value: stringify(input), inline: false }];
    }
    return Object.entries(input as Record<string, unknown>).map(([key, raw]) => {
      const value = stringify(raw);
      return { key, value, inline: value.length <= INLINE_MAX && !value.includes('\n') };
    });
  }

  let inputFields = $derived(toFields(call.input));
  let contentFields = $derived(
    Array.isArray(call.content) && call.content.length > 0
      ? [{ key: '', value: stringify(call.content), inline: false }]
      : []
  );

  let hasDetails = $derived(inputFields.length > 0 || contentFields.length > 0 || output != null);

  /** Byte size of the output, as an honest signal of what is about to unfold. */
  function formatSize(text: string): string {
    const bytes = new TextEncoder().encode(text).length;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatContent(content: string | unknown[] | undefined): string {
    if (content == null) return '';
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content.map(block => {
        if (typeof block === 'object' && block !== null && 'text' in (block as Record<string, unknown>)) {
          return (block as Record<string, unknown>).text as string;
        }
        try {
          return JSON.stringify(block, null, 2);
        } catch {
          return String(block);
        }
      }).join('\n');
    }
    return String(content);
  }
</script>

{#snippet fields(list: Field[])}
  <dl class="tool-fields">
    {#each list as field (field.key)}
      {#if field.inline}
        <div class="tool-field tool-field-inline">
          {#if field.key}<dt class="tool-key">{field.key}</dt>{/if}
          <dd class="tool-value">{field.value}</dd>
        </div>
      {:else}
        <div class="tool-field">
          {#if field.key}<dt class="tool-key">{field.key}</dt>{/if}
          <dd class="tool-value"><pre class="tool-pre">{field.value}</pre></dd>
        </div>
      {/if}
    {/each}
  </dl>
{/snippet}

{#snippet body()}
  {#if inputFields.length > 0}
    <div class="tool-section">
      <div class="tool-section-label">Input</div>
      {@render fields(inputFields)}
    </div>
  {/if}
  {#if contentFields.length > 0}
    <div class="tool-section">
      <div class="tool-section-label">Content</div>
      {@render fields(contentFields)}
    </div>
  {/if}
  {#if output != null}
    <div class="tool-section">
      <div class="tool-section-label">
        {isError ? 'Error' : 'Result'}
        <span class="tool-section-size">{formatSize(output)}</span>
      </div>
      {#if diffLinesOut.length > 0}
        <pre class="tool-pre tool-diff">{#each diffLinesOut as line, i (i)}<span class="dl dl-{line.kind}">{line.text}
</span>{/each}</pre>
      {:else}
        <pre class="tool-pre" class:tool-pre-error={isError}>{output}</pre>
      {/if}
    </div>
  {/if}
{/snippet}

{#if !showRow}
  <div class="tool-group" data-status={displayStatus}>
    {@render body()}
  </div>
{:else if hasDetails}
  <details class="tool-group" data-status={displayStatus}>
    <summary class="tool-row">
      <span class="tool-glyph" class:running={isRunning} class:failed={isError}>{glyph}</span>
      {#if call.server}<span class="tool-server">{call.server}</span>{/if}
      <span class="tool-name">{title}</span>
      <span class="tool-subject">{call.subject}</span>
      {#if errorPreview}<span class="tool-error-preview">{errorPreview}</span>{/if}
      <span class="tool-meta">
        {#if showStat}
          {#if stat!.added > 0}<span class="tool-stat-add">+{stat!.added}</span>{/if}
          {#if stat!.removed > 0}<span class="tool-stat-del">−{stat!.removed}</span>{/if}
        {/if}
        {#if showExitCode}<span class="tool-exit">exit {call.exitCode}</span>{/if}
      </span>
      <span class="tool-chevron">{'›'}</span>
    </summary>
    <div class="tool-body">{@render body()}</div>
  </details>
{:else}
  <div class="tool-group" data-status={displayStatus}>
    <div class="tool-row tool-row-static">
      <span class="tool-glyph" class:running={isRunning} class:failed={isError}>{glyph}</span>
      {#if call.server}<span class="tool-server">{call.server}</span>{/if}
      <span class="tool-name">{title}</span>
      <span class="tool-subject">{call.subject}</span>
      {#if errorPreview}<span class="tool-error-preview">{errorPreview}</span>{/if}
      <span class="tool-meta">
        {#if showStat}
          {#if stat!.added > 0}<span class="tool-stat-add">+{stat!.added}</span>{/if}
          {#if stat!.removed > 0}<span class="tool-stat-del">−{stat!.removed}</span>{/if}
        {/if}
        {#if showExitCode}<span class="tool-exit">exit {call.exitCode}</span>{/if}
      </span>
    </div>
  </div>
{/if}

<style>
  /* Tool results are the quietest tier: no border, no fill, no status tint.
     Colour is reserved for failure and in-flight. */
  .tool-group {
    width: 100%;
    min-width: 0;
    border-radius: var(--radius-sm);
  }

  .tool-row {
    display: flex;
    align-items: baseline;
    gap: 0.375rem;
    padding: 0.1875rem 0.375rem;
    line-height: 1.3;
    cursor: pointer;
    list-style: none;
    border-radius: var(--radius-sm);
  }

  .tool-row::-webkit-details-marker {
    display: none;
  }

  .tool-row-static {
    cursor: default;
  }

  .tool-row:hover {
    background: hsl(var(--muted) / 0.35);
  }

  .tool-row-static:hover {
    background: none;
  }

  .tool-glyph {
    flex-shrink: 0;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-success));
    line-height: 1;
  }

  .tool-glyph.failed {
    color: hsl(var(--status-danger));
  }

  .tool-glyph.running {
    color: hsl(var(--status-working));
    border-radius: 50%;
    animation: tool-pulse 1.6s ease-out infinite;
  }

  /* Pulse via box-shadow rather than opacity, which flickers. */
  @keyframes tool-pulse {
    0% { box-shadow: 0 0 0 0 hsl(var(--status-working) / 0.55); }
    70% { box-shadow: 0 0 0 4px hsl(var(--status-working) / 0); }
    100% { box-shadow: 0 0 0 0 hsl(var(--status-working) / 0); }
  }

  .tool-server {
    flex-shrink: 0;
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--muted-foreground));
  }

  .tool-name {
    flex-shrink: 0;
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--foreground));
  }

  /* Takes its natural width so the error preview can sit right beside it. When
     there is no preview it still absorbs the slack, via the sibling rule below. */
  .tool-subject {
    flex: 0 1 auto;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Absorbs the row's slack so the message reads as a continuation of the
     command rather than a stranded right-hand column. */
  .tool-error-preview {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-danger));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* `margin-left: auto` keeps the trailing figures hard right whether or not an
     error preview is present to absorb the slack. */
  .tool-meta {
    flex-shrink: 0;
    display: inline-flex;
    align-items: baseline;
    gap: 0.375rem;
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 0.625rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }


  .tool-stat-add {
    color: hsl(var(--status-success));
  }

  .tool-stat-del {
    color: hsl(var(--status-danger));
  }

  /* Diff colouring uses the semantic status tokens, so light and dark are
     correct by construction — no separate diff theme to keep in sync. */
  .tool-diff .dl {
    display: block;
  }

  .tool-diff .dl-add {
    background: hsl(var(--status-success) / 0.12);
    color: hsl(var(--status-success));
  }

  .tool-diff .dl-del {
    background: hsl(var(--status-danger) / 0.12);
    color: hsl(var(--status-danger));
  }

  .tool-diff .dl-hunk {
    color: hsl(var(--status-working));
  }

  .tool-diff .dl-meta {
    color: hsl(var(--muted-foreground));
  }

  .tool-exit {
    color: hsl(var(--status-danger));
  }

  .tool-chevron {
    flex-shrink: 0;
    font-size: 0.5rem;
    color: hsl(var(--muted-foreground));
    transition: transform 0.15s;
  }

  details[open] .tool-chevron {
    transform: rotate(90deg);
  }

  .tool-body {
    padding: 0.125rem 0.375rem 0.375rem 1.375rem;
  }

  .tool-section {
    margin-top: 0.25rem;
  }

  .tool-section-label {
    display: flex;
    align-items: baseline;
    gap: 0.375rem;
    font-size: 0.625rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    margin-bottom: 0.125rem;
  }

  .tool-section-size {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    text-transform: none;
    letter-spacing: 0;
    opacity: 0.75;
  }

  .tool-fields {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
  }

  .tool-field {
    margin: 0;
    min-width: 0;
  }

  /* Short scalars sit beside their key; long or multi-line values get a block. */
  .tool-field-inline {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }

  .tool-key {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 0.625rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .tool-field-inline .tool-key {
    min-width: 5rem;
  }

  .tool-value {
    margin: 0;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    color: hsl(var(--foreground));
    overflow-wrap: anywhere;
  }

  .tool-field:not(.tool-field-inline) .tool-key {
    display: block;
    margin-bottom: 0.125rem;
  }

  .tool-pre {
    padding: 0.375rem 0.5rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.3);
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    line-height: 1.4;
    color: hsl(var(--foreground));
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 20rem;
    overflow-y: auto;
  }

  .tool-pre-error {
    color: hsl(var(--status-danger));
  }
</style>
