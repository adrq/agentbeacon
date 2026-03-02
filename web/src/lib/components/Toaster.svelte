<script lang="ts">
  import { toasts } from '../stores/toasts';
</script>

{#if $toasts.length > 0}
  <div class="toaster" aria-live="polite">
    {#each $toasts as toast (toast.id)}
      <button class="toast toast-{toast.type}" onclick={() => toasts.dismiss(toast.id)}>
        <span class="toast-icon">
          {#if toast.type === 'success'}&#x2713;{:else if toast.type === 'error'}&#x2717;{:else}&#x2139;{/if}
        </span>
        <span class="toast-message">{toast.message}</span>
      </button>
    {/each}
  </div>
{/if}

<style>
  .toaster {
    position: fixed;
    bottom: 1rem;
    right: 1rem;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: 360px;
    pointer-events: none;
  }

  .toast {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.625rem 1rem;
    border-radius: var(--radius);
    font-size: 0.8125rem;
    font-weight: 500;
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    border: 1px solid hsl(var(--border));
    box-shadow: 0 4px 12px hsl(0 0% 0% / 0.15);
    cursor: pointer;
    pointer-events: auto;
    text-align: left;
    animation: toast-in 0.2s ease-out;
  }

  .toast-icon {
    flex-shrink: 0;
    font-size: 0.875rem;
    width: 1.25rem;
    text-align: center;
  }

  .toast-success {
    border-left: 3px solid hsl(var(--status-success));
  }

  .toast-success .toast-icon {
    color: hsl(var(--status-success));
  }

  .toast-error {
    border-left: 3px solid hsl(var(--status-danger));
  }

  .toast-error .toast-icon {
    color: hsl(var(--status-danger));
  }

  .toast-info {
    border-left: 3px solid hsl(var(--primary));
  }

  .toast-info .toast-icon {
    color: hsl(var(--primary));
  }

  .toast-message {
    flex: 1;
    min-width: 0;
  }

  @keyframes toast-in {
    from {
      opacity: 0;
      transform: translateY(0.5rem);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
</style>
