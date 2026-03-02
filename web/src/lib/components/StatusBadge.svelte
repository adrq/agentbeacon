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
    border-radius: var(--radius-sm);
    white-space: nowrap;
  }

  .badge.small {
    padding: 0.125rem 0.5rem;
    font-size: 0.6875rem;
  }

  .badge.medium {
    padding: 0.25rem 0.625rem;
    font-size: 0.6875rem;
  }

  .dot {
    width: 0.375rem;
    height: 0.375rem;
    flex-shrink: 0;
    position: relative;
  }

  .dot::before, .dot::after {
    content: '';
    position: absolute;
  }

  /* Working: filled pulsing circle */
  .working { background: hsl(var(--status-working) / 0.15); color: hsl(var(--status-working)); }
  .working .dot {
    background: hsl(var(--status-working));
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
    box-shadow: 0 0 2px 1px hsl(var(--status-working) / 0.15);
  }

  /* Submitted: hollow circle */
  .submitted { background: hsl(var(--muted)); color: hsl(var(--muted-foreground)); }
  .submitted .dot {
    background: transparent;
    border: 1.5px solid currentColor;
    border-radius: 50%;
  }

  /* Completed: checkmark */
  .completed { background: hsl(var(--status-success) / 0.15); color: hsl(var(--status-success)); }
  .completed .dot {
    background: transparent;
    border-radius: 0;
  }
  .completed .dot::after {
    width: 3px;
    height: 5px;
    border-right: 1.5px solid currentColor;
    border-bottom: 1.5px solid currentColor;
    transform: rotate(45deg);
    top: -1px;
    left: 1px;
  }

  /* Failed: X mark */
  .failed { background: hsl(var(--status-danger) / 0.15); color: hsl(var(--status-danger)); }
  .failed .dot {
    background: transparent;
    border-radius: 0;
  }
  .failed .dot::before, .failed .dot::after {
    width: 1.5px;
    height: 6px;
    background: currentColor;
    left: 50%;
    top: 50%;
    border-radius: 1px;
  }
  .failed .dot::before { transform: translate(-50%, -50%) rotate(45deg); }
  .failed .dot::after { transform: translate(-50%, -50%) rotate(-45deg); }

  /* Canceled: horizontal dash */
  .canceled { background: hsl(var(--muted)); color: hsl(var(--muted-foreground)); }
  .canceled .dot {
    background: currentColor;
    border-radius: 1px;
    height: 2px;
    width: 6px;
    align-self: center;
  }

  /* Input-required: diamond */
  .input-required { background: hsl(var(--status-attention) / 0.15); color: hsl(var(--status-attention)); }
  .input-required .dot {
    background: currentColor;
    clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);
  }

  /* Turn-complete: hollow circle (muted) */
  .input-required.turn-complete {
    background: hsl(var(--muted));
    color: hsl(var(--muted-foreground));
  }
  .input-required.turn-complete .dot {
    background: transparent;
    clip-path: none;
    border: 1.5px solid currentColor;
    border-radius: 50%;
  }

  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 2px 1px hsl(var(--status-working) / 0.15); }
    50% { box-shadow: 0 0 6px 2px hsl(var(--status-working) / 0.4); }
  }
</style>
