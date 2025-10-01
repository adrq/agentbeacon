<script lang="ts">
  import { api } from '../api';
  import { onMount, onDestroy } from 'svelte';

  type StatusType = 'checking' | 'ready' | 'not_ready' | 'error';

  let status: StatusType = $state('checking');
  let lastCheck: Date | null = $state(null);
  let intervalId: number | undefined;

  async function checkStatus() {
    try {
      const result = await api.checkReady();
      lastCheck = new Date();

      if (result.ok && result.status === 'ready') {
        status = 'ready';
      } else if (result.ok && result.status === 'not_ready') {
        status = 'not_ready';
      } else {
        status = 'error';
      }
    } catch (error) {
      status = 'error';
      lastCheck = new Date();
    }
  }

  onMount(() => {
    checkStatus(); // Immediate check
    intervalId = setInterval(checkStatus, 10000); // Poll every 10 seconds
  });

  onDestroy(() => {
    if (intervalId !== undefined) {
      clearInterval(intervalId);
    }
  });

  // Computed values for display
  const dotColor = $derived(() => {
    switch (status) {
      case 'ready':
        return 'bg-green-500';
      case 'not_ready':
        return 'bg-orange-500';
      case 'error':
        return 'bg-red-500';
      case 'checking':
      default:
        return 'bg-gray-400';
    }
  });

  const statusLabel = $derived(() => {
    switch (status) {
      case 'ready':
        return 'Ready';
      case 'not_ready':
        return 'Not Ready';
      case 'error':
        return 'Disconnected';
      case 'checking':
      default:
        return 'Checking...';
    }
  });

  const tooltipText = $derived(() => {
    if (!lastCheck) return 'Checking scheduler status...';
    const timeStr = lastCheck.toLocaleTimeString();
    return `Last checked: ${timeStr}`;
  });
</script>

<div
  class="flex items-center gap-2"
  title={tooltipText()}
  role="status"
  aria-live="polite"
>
  <div
    class="w-2 h-2 rounded-full {dotColor()}"
    aria-hidden="true"
  ></div>
  <span class="text-xs text-muted-foreground font-medium">
    {statusLabel()}
  </span>
</div>
