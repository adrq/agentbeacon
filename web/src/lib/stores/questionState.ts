// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { writable, derived, get } from 'svelte/store';
import type { QuestionState } from '../questions';
import type { DecisionBatchResponse, DecisionSummaryResponse } from '../types';
import { api } from '../api';

export interface DecisionBatch {
  eventId: string;
  batchId: string;
  executionId: string;
  sessionId: string;
  executionTitle: string | null;
  status: 'pending' | 'answered' | 'dismissed' | 'expired';
  importance: 'blocking' | 'fyi';
  questions: QuestionState[];
  answer: string | null;
  answeredAt: string | null;
  dismissedAt: string | null;
  truncated: boolean;
  createdAt: string;
}

// A resolved decision as history renders it: no per-kind status, no answer text.
export interface DecisionSummary {
  eventId: string;
  batchId: string;
  executionId: string;
  executionTitle: string | null;
  sessionId: string;
  status: 'resolved';
  importance: 'blocking' | 'fyi';
  questionPreview: string;
  questionCount: number;
  createdAt: string;
}

// Legacy alias for components that still reference the old name
export type DecisionItem = DecisionBatch;

// Maps a brief.
function mapResponseToBatch(d: DecisionBatchResponse): DecisionBatch {
  return {
    eventId: d.event_id,
    batchId: d.batch_id,
    executionId: d.execution_id,
    sessionId: d.session_id,
    executionTitle: d.execution_title,
    status: d.status,
    importance: d.importance,
    questions: d.questions.map(q => ({
      questionText: q.question,
      context: q.context ?? undefined,
      options: q.options ?? undefined,
      answer: '',
    })),
    answer: d.answer,
    answeredAt: d.answered_at,
    dismissedAt: d.dismissed_at,
    truncated: d.truncated ?? false,
    createdAt: d.created_at,
  };
}

function mapResponseToSummary(d: DecisionSummaryResponse): DecisionSummary {
  return {
    eventId: d.event_id,
    batchId: d.batch_id,
    executionId: d.execution_id,
    executionTitle: d.execution_title,
    sessionId: d.session_id,
    status: 'resolved',
    importance: d.importance,
    questionPreview: d.question_preview,
    questionCount: d.question_count,
    createdAt: d.created_at,
  };
}

// Pending briefs, replaced on each refresh.
export const pendingDecisions = writable<DecisionBatch[]>([]);
// Every resolved summary fetched so far, appended page by page.
export const resolvedHistory = writable<DecisionSummary[]>([]);
// Resolved history as it renders: the summaries the pending list does not hold.
export const pastDecisions = derived(
  [resolvedHistory, pendingDecisions],
  ([$history, $pending]) => {
    const pendingIds = new Set($pending.map(d => d.eventId));
    return $history.filter(d => !pendingIds.has(d.eventId));
  },
);
export const pastDecisionsCursor = writable<string | null>(null);
export const pastDecisionsHasMore = writable(false);
// Set when the first page of history could not be read. Cleared by a success.
export const pastDecisionsError = writable<string | null>(null);

// Last ETag seen for the pending list.
let pendingEtag: string | null = null;
// The current refresh generation. Only its response is applied.
let pendingGeneration = 0;
let refreshInFlight = false;
// The current history generation. Only the newest walk's response is applied.
let resolvedGeneration = 0;

export function setDecisionsFromResponse(decisions: DecisionBatchResponse[]) {
  // Ascending creation position from the server, newest first here.
  pendingDecisions.set(decisions.map(mapResponseToBatch).reverse());
}

export const pendingCount = derived(pendingDecisions, $d => $d.length);

/**
 * The outcome of a refresh.
 *
 * `stale` means nothing was applied. It is neither success nor failure.
 */
export type RefreshResult =
  | { status: 'applied' }
  | { status: 'stale' }
  | { status: 'failed'; error: unknown };

/**
 * Refresh the pending list. A 304 leaves the current list untouched.
 *
 * With `skipIfInFlight`, a refresh already in progress is left to finish and
 * this call returns `stale`.
 */
export async function refreshDecisions(
  options?: { skipIfInFlight?: boolean },
): Promise<RefreshResult> {
  if (options?.skipIfInFlight && refreshInFlight) return { status: 'stale' };
  const generation = ++pendingGeneration;
  refreshInFlight = true;
  try {
    const result = await api.getDecisionsPending({ etag: pendingEtag });
    // A response that lost the race is dropped whole, body and validator.
    if (generation !== pendingGeneration) return { status: 'stale' };
    if (result.notModified) return { status: 'applied' };
    pendingEtag = result.etag;
    setDecisionsFromResponse(result.body.decisions);
    return { status: 'applied' };
  } catch (error) {
    if (generation !== pendingGeneration) return { status: 'stale' };
    return { status: 'failed', error };
  } finally {
    // Only the newest request re-opens the gate.
    if (generation === pendingGeneration) refreshInFlight = false;
  }
}

/** Fetch one resolved decision's detail: the true kind and its answer text. */
export async function fetchDecisionDetail(eventId: string): Promise<DecisionBatch> {
  return mapResponseToBatch(await api.getDecision(eventId));
}

/** Append the next page of resolved history. */
export async function loadMorePastDecisions(reset = false) {
  const before = reset ? undefined : (get(pastDecisionsCursor) ?? undefined);
  const generation = ++resolvedGeneration;
  let page;
  try {
    page = await api.getDecisionsResolved({ before });
  } catch (error) {
    // Only the newest walk publishes anything, failure included.
    if (generation !== resolvedGeneration) return;
    // Nothing is published on failure: an emptied, exhausted store would be
    // indistinguishable from a history that is genuinely empty.
    if (reset) {
      pastDecisionsError.set(error instanceof Error ? error.message : 'Failed to load history');
    }
    throw error;
  }
  if (generation !== resolvedGeneration) return;
  pastDecisionsError.set(null);
  const items = page.items.map(mapResponseToSummary);
  resolvedHistory.update(current => (reset ? items : [...current, ...items]));
  pastDecisionsCursor.set(page.next_cursor);
  pastDecisionsHasMore.set(page.has_more);
}

// True when polling has failed consecutively; cleared on next success
export const decisionsStale = writable(false);

// Backward-compatible exports used by existing components
export const decisionItems = pendingDecisions;
export const visibleDecisionItems = pendingDecisions;
export const decisionCount = pendingCount;

export const executionsWithQuestions = derived(
  pendingDecisions,
  ($pending) => new Set($pending.map(d => d.executionId))
);

// Session-level in-flight guard: prevents concurrent POST /sessions/{id}/message
// when multiple batches are pending on the same session.
const submittingSessions = new Set<string>();

export function tryClaimSubmit(sessionId: string, _batchId: string): boolean {
  if (submittingSessions.has(sessionId)) return false;
  submittingSessions.add(sessionId);
  return true;
}

export function releaseSubmit(sessionId: string, _batchId: string) {
  submittingSessions.delete(sessionId);
}

// These are no-ops now — server handles state via dismiss endpoint and answer events
export function markBatchSubmitted(_sessionId: string, _batchId: string) {}
export function suppressSession(_sessionId: string, _batchId: string) {}

// submittedBatches and suppressedSessions are kept as empty stores for components
// that still reference them; they're effectively dead state now.
export const submittedBatches = writable<Record<string, string>>({});
export const suppressedSessions = writable<Record<string, string>>({});

// Callback hook for notifications — set by standalone adapter
type NewDecisionCallback = (item: DecisionBatch) => void;
let onNewDecisionCallback: NewDecisionCallback | null = null;

export function setOnNewDecisionCallback(cb: NewDecisionCallback | null) {
  onNewDecisionCallback = cb;
}

export function notifyNewDecision(item: DecisionBatch) {
  onNewDecisionCallback?.(item);
}
