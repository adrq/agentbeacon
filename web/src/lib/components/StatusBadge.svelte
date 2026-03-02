<script lang="ts">
  import type { ExecutionStatus } from '../types';

  interface Props {
    status: ExecutionStatus;
    size?: 'small' | 'medium';
    hasQuestions?: boolean;
  }

  let { status, size = 'medium', hasQuestions }: Props = $props();

  const labels: Record<ExecutionStatus, string> = {
    'submitted': 'Submitted',
    'working': 'Working',
    'input-required': 'Awaiting Input',
    'completed': 'Completed',
    'failed': 'Failed',
    'canceled': 'Canceled',
  };

  let label = $derived(
    status === 'input-required' && hasQuestions === false
      ? 'Turn Complete'
      : labels[status]
  );

  let turnComplete = $derived(status === 'input-required' && hasQuestions === false);
</script>

<span class="badge {status} {size}" class:turn-complete={turnComplete}>
  <span class="dot"></span>
  {label}
</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    font-weight: 500;
    border-radius: 0.25rem;
    white-space: nowrap;
  }

  .badge.small {
    padding: 0.125rem 0.5rem;
    font-size: 0.6875rem;
  }

  .badge.medium {
    padding: 0.25rem 0.625rem;
    font-size: 0.75rem;
  }

  .dot {
    width: 0.375rem;
    height: 0.375rem;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .submitted { background: hsl(var(--muted)); color: hsl(var(--muted-foreground)); }
  .submitted .dot { background: hsl(var(--muted-foreground)); }

  .working { background: hsl(var(--status-working) / 0.15); color: hsl(var(--status-working)); }
  .working .dot {
    background: hsl(var(--status-working));
    animation: pulse 2s ease-in-out infinite;
    box-shadow: 0 0 2px 1px hsl(var(--status-working) / 0.15);
  }

  .input-required { background: hsl(var(--status-attention) / 0.15); color: hsl(var(--status-attention)); }
  .input-required .dot {
    background: hsl(var(--status-attention));
    box-shadow: 0 0 4px 1px hsl(var(--status-attention) / 0.5);
  }

  .input-required.turn-complete {
    background: hsl(var(--muted));
    color: hsl(var(--muted-foreground));
  }

  .input-required.turn-complete .dot {
    background: hsl(var(--muted-foreground));
    box-shadow: none;
  }

  .completed { background: hsl(var(--status-success) / 0.15); color: hsl(var(--status-success)); }
  .completed .dot { background: hsl(var(--status-success)); }

  .failed { background: hsl(var(--status-danger) / 0.15); color: hsl(var(--status-danger)); }
  .failed .dot { background: hsl(var(--status-danger)); }

  .canceled { background: hsl(var(--muted)); color: hsl(var(--muted-foreground)); }
  .canceled .dot { background: hsl(var(--muted-foreground)); }

  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 2px 1px hsl(var(--status-working) / 0.15); }
    50% { box-shadow: 0 0 6px 2px hsl(var(--status-working) / 0.4); }
  }
</style>
