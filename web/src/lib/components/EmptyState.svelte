<script lang="ts">
</script>

<div class="empty-state">
  <div class="empty-icon">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 9h6" />
      <path d="M9 13h4" />
    </svg>
  </div>
  <p class="empty-title">No executions yet</p>
  <p class="empty-hint">Create your first execution to get started.</p>
</div>

<style>
  .empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .empty-icon {
    width: 3rem;
    height: 3rem;
    opacity: 0.4;
  }

  .empty-icon svg {
    width: 100%;
    height: 100%;
  }

  .empty-title {
    font-size: 0.9375rem;
    font-weight: 500;
    color: hsl(var(--foreground));
    opacity: 0.7;
  }

  .empty-hint {
    font-size: 0.8125rem;
  }
</style>
