<script lang="ts">
  let { startTime, endTime = null }: { startTime: string; endTime?: string | null } = $props();
  let display = $state('');

  $effect(() => {
    const start = startTime;
    const end = endTime;
    if (end) {
      display = formatElapsed(new Date(start), new Date(end));
      return;
    }
    display = formatElapsed(new Date(start), new Date());
    const id = setInterval(() => {
      display = formatElapsed(new Date(start), new Date());
    }, 1000);
    return () => clearInterval(id);
  });

  function formatElapsed(start: Date, now: Date): string {
    const secs = Math.max(0, Math.floor((now.getTime() - start.getTime()) / 1000));
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ${secs % 60}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }
</script>

<span class="elapsed-time">{display}</span>

<style>
  .elapsed-time {
    font-size: 0.6875rem;
    font-variant-numeric: tabular-nums;
    color: hsl(var(--muted-foreground));
  }
</style>
