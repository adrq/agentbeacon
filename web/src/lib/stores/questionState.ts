import { writable, derived } from 'svelte/store';
import type { QuestionState } from '../questions';
import type { DecisionBatchResponse } from '../types';
import { api } from '../api';

export interface DecisionBatch {
  batchId: string;
  executionId: string;
  sessionId: string;
  executionTitle: string | null;
  agentName: string;
  hierarchicalName: string;
  status: 'pending' | 'answered' | 'dismissed' | 'expired';
  importance: 'blocking' | 'fyi';
  questions: QuestionState[];
  answer: string | null;
  answeredAt: string | null;
  dismissedAt: string | null;
  truncated: boolean;
  createdAt: string;
}

// Legacy alias for components that still reference the old name
export type DecisionItem = DecisionBatch;

function mapResponseToBatch(d: DecisionBatchResponse): DecisionBatch {
  return {
    batchId: d.batch_id,
    executionId: d.execution_id,
    sessionId: d.session_id,
    executionTitle: d.execution_title,
    agentName: d.agent_name,
    hierarchicalName: d.hierarchical_name,
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

// All decisions from server (populated by DecisionStateProvider)
export const allDecisions = writable<DecisionBatch[]>([]);

export function setDecisionsFromResponse(decisions: DecisionBatchResponse[]) {
  allDecisions.set(decisions.map(mapResponseToBatch));
}

export const pendingDecisions = derived(allDecisions, $d =>
  $d.filter(d => d.status === 'pending')
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
);
export const pastDecisions = derived(allDecisions, $d =>
  $d.filter(d => d.status !== 'pending')
    .sort((a, b) => {
      const aTime = new Date(a.answeredAt ?? a.dismissedAt ?? a.createdAt).getTime();
      const bTime = new Date(b.answeredAt ?? b.dismissedAt ?? b.createdAt).getTime();
      return bTime - aTime;
    })
);
export const pendingCount = derived(pendingDecisions, $d => $d.length);

export async function refreshDecisions() {
  try {
    const resp = await api.getDecisions();
    setDecisionsFromResponse(resp.decisions);
  } catch { /* polling will catch up */ }
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
