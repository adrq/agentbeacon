<script lang="ts">
  import DecisionQueue from './DecisionQueue.svelte';

  interface Props {
    collapsed: boolean;
    onToggle: () => void;
    decisionCount: number;
    wide?: boolean;
  }

  let { collapsed, onToggle, decisionCount, wide = false }: Props = $props();
</script>

<aside class="action-panel" class:collapsed class:wide aria-label="Decisions panel">
  {#if collapsed}
    <button class="collapsed-strip" onclick={onToggle} aria-label="Expand decisions panel">
      <div class="collapsed-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
          <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
        {#if decisionCount > 0}
          <span class="collapsed-badge">{decisionCount > 9 ? '9+' : decisionCount}</span>
        {/if}
      </div>
      <svg class="expand-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>
  {:else}
    <div class="panel-header">
      <span class="panel-title">DECISIONS ({decisionCount})</span>
      <button class="collapse-btn" onclick={onToggle} aria-label="Collapse decisions panel">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
    </div>
    <div class="panel-body scroll-thin">
      <DecisionQueue />
    </div>
  {/if}
</aside>

<style>
  .action-panel {
    flex: 0 0 320px;
    display: flex;
    flex-direction: column;
    border-left: 1px solid hsl(var(--border));
    background: hsl(var(--background));
    overflow: hidden;
    transition: flex-basis 0.15s ease;
  }

  .action-panel.collapsed {
    flex: 0 0 40px;
  }

  .collapsed-strip {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    padding: 0.5rem 0;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: color 0.15s, background 0.15s;
  }

  .collapsed-strip:hover {
    background: hsl(var(--muted) / 0.3);
    color: hsl(var(--foreground));
  }

  .collapsed-icon {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .collapsed-icon svg {
    width: 18px;
    height: 18px;
  }

  .collapsed-badge {
    position: absolute;
    top: -6px;
    right: -8px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: hsl(var(--status-danger));
    color: hsl(0 0% 100%);
    font-size: 0.625rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
  }

  .expand-chevron {
    width: 14px;
    height: 14px;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid hsl(var(--border));
    flex-shrink: 0;
  }

  .panel-title {
    font-size: 0.6875rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
  }

  .collapse-btn {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: color 0.15s, background 0.15s;
  }

  .collapse-btn:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .collapse-btn svg {
    width: 14px;
    height: 14px;
  }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .action-panel.wide {
    flex: 1 1 0;
    min-width: 0;
    transition: none;
  }

  @media (max-width: 768px) {
    .action-panel {
      display: none;
    }
  }
</style>
