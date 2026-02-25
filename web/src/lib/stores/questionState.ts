import { writable, derived } from 'svelte/store';
import type { QuestionState } from '../questions';

export interface DecisionItem {
  sessionId: string;
  executionId: string;
  executionTitle: string | null;
  agentName: string;
  batchId: string;
  questions: QuestionState[];
  createdAt: string;
}

// Written to by QuestionStateProvider component
export const decisionItems = writable<DecisionItem[]>([]);
// Value = batchId at time of suppression (unique per escalate invocation)
export const suppressedSessions = writable<Record<string, string>>({});

// Cross-surface submission guard: tracks which session+batch combos have been submitted
// from any UI surface (ActionPanel or QuestionBanner) to prevent double submission.
export const submittedBatches = writable<Record<string, string>>({});

export function markBatchSubmitted(sessionId: string, batchId: string) {
  submittedBatches.update(s => ({ ...s, [sessionId]: batchId }));
}

// In-flight guard: prevents race where both surfaces fire submitAnswer before
// markBatchSubmitted runs. Synchronous claim/release keyed by sessionId+batchId.
const submittingBatches = new Map<string, string>();

export function tryClaimSubmit(sessionId: string, batchId: string): boolean {
  if (submittingBatches.get(sessionId) === batchId) return false;
  submittingBatches.set(sessionId, batchId);
  return true;
}

export function releaseSubmit(sessionId: string, batchId: string) {
  if (submittingBatches.get(sessionId) === batchId) submittingBatches.delete(sessionId);
}

export const visibleDecisionItems = derived(
  [decisionItems, suppressedSessions, submittedBatches],
  ([$items, $suppressed, $submitted]) => $items.filter(i => {
    if ($suppressed[i.sessionId] === i.batchId) return false;
    if ($submitted[i.sessionId] === i.batchId) return false;
    return true;
  })
);

export const decisionCount = derived(
  visibleDecisionItems,
  ($visible) => $visible.length
);

export const executionsWithQuestions = derived(
  visibleDecisionItems,
  ($visible) => new Set($visible.map(i => i.executionId))
);

// Executions whose session events were fetched successfully with no questions.
// Written by QuestionStateProvider each cycle; rebuilt from scratch so stale
// entries disappear when the execution leaves input-required.
export const noQuestionExecutions = writable<Set<string>>(new Set());

export function suppressSession(sessionId: string, batchId: string) {
  suppressedSessions.update(s => ({ ...s, [sessionId]: batchId }));
}

// Callback hook for notifications — set by standalone adapter
type NewDecisionCallback = (item: DecisionItem) => void;
let onNewDecisionCallback: NewDecisionCallback | null = null;

export function setOnNewDecisionCallback(cb: NewDecisionCallback | null) {
  onNewDecisionCallback = cb;
}

export function notifyNewDecision(item: DecisionItem) {
  onNewDecisionCallback?.(item);
}
