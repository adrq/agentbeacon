<script lang="ts">
  import { api } from '../api';
  import { toasts } from '../stores/toasts';
  import {
    setDecisionsFromResponse,
    notifyNewDecision,
    decisionsStale,
    type DecisionBatch,
  } from '../stores/questionState';

  let prevPendingIds = new Set<string>();
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let consecutiveFailures = 0;

  async function fetchDecisions() {
    try {
      const resp = await api.getDecisions();
      setDecisionsFromResponse(resp.decisions);
      if (consecutiveFailures > 0) {
        decisionsStale.set(false);
      }
      consecutiveFailures = 0;

      // Notify about newly appeared pending items
      const currentPending: DecisionBatch[] = [];
      for (const d of resp.decisions) {
        if (d.status === 'pending') {
          currentPending.push({
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
            createdAt: d.created_at,
          });
        }
      }
      const newIds = new Set(currentPending.map(d => d.batchId));
      for (const item of currentPending) {
        if (!prevPendingIds.has(item.batchId)) {
          notifyNewDecision(item);
        }
      }
      prevPendingIds = newIds;
    } catch {
      consecutiveFailures++;
      if (consecutiveFailures === 3) {
        decisionsStale.set(true);
        toasts.error('Decision polling failed — sidebar may show stale data');
      }
    }
  }

  $effect(() => {
    fetchDecisions();
    pollTimer = setInterval(fetchDecisions, 5000);
    return () => {
      if (pollTimer) clearInterval(pollTimer);
    };
  });
</script>
