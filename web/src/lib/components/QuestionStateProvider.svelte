<script lang="ts">
  import { get } from 'svelte/store';
  import { toasts } from '../stores/toasts';
  import {
    pendingDecisions,
    refreshDecisions,
    loadMorePastDecisions,
    notifyNewDecision,
    decisionsStale,
  } from '../stores/questionState';

  let prevPendingIds = new Set<string>();
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let consecutiveFailures = 0;

  function recordFailure() {
    consecutiveFailures++;
    if (consecutiveFailures === 3) {
      decisionsStale.set(true);
      toasts.error('Decision polling failed — sidebar may show stale data');
    }
  }

  async function fetchDecisions() {
    try {
      const result = await refreshDecisions({ skipIfInFlight: true });
      // A stale outcome neither clears the warning nor counts against it.
      if (result.status === 'stale') return;
      if (result.status === 'failed') {
        recordFailure();
        return;
      }
      if (consecutiveFailures > 0) {
        decisionsStale.set(false);
      }
      consecutiveFailures = 0;

      // Notify about newly appeared pending items.
      const currentPending = get(pendingDecisions);
      const newIds = new Set(currentPending.map(d => d.eventId));
      for (const item of currentPending) {
        if (!prevPendingIds.has(item.eventId)) {
          notifyNewDecision(item);
        }
      }
      prevPendingIds = newIds;
    } catch {
      recordFailure();
    }
  }

  $effect(() => {
    fetchDecisions();
    loadMorePastDecisions(true).catch(() => { /* history is best-effort */ });
    pollTimer = setInterval(fetchDecisions, 5000);
    return () => {
      if (pollTimer) clearInterval(pollTimer);
    };
  });
</script>
