<script lang="ts">
  import { currentScreen } from '../stores/appState';
  import { decisionCount } from '../stores/questionState';

  interface Props {
    onToggleDecisions?: () => void;
    panelOpen?: boolean;
  }

  let { onToggleDecisions, panelOpen = false }: Props = $props();

  const navItems: { label: string; icon: string; screens: string[]; hash: string }[] = [
    { label: 'Executions', icon: 'list', screens: ['Home', 'ExecutionDetail'], hash: '#/' },
    { label: 'Projects', icon: 'folder', screens: ['Projects', 'ProjectDetail'], hash: '#/projects' },
    { label: 'Agents', icon: 'bot', screens: ['Agents'], hash: '#/agents' },
  ];

  function navigate(hash: string) {
    window.location.hash = hash;
  }
</script>

<nav class="nav-rail" aria-label="Main navigation">
  {#each navItems as item}
    <button
      class="nav-rail-item"
      class:active={item.screens.includes($currentScreen)}
      aria-label={item.label}
      aria-current={item.screens.includes($currentScreen) ? 'page' : undefined}
      onclick={() => navigate(item.hash)}
    >
      {#if item.icon === 'list'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="8" y1="6" x2="21" y2="6" />
          <line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" />
          <line x1="3" y1="6" x2="3.01" y2="6" />
          <line x1="3" y1="12" x2="3.01" y2="12" />
          <line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
      {:else if item.icon === 'folder'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      {:else if item.icon === 'bot'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2" />
          <circle cx="12" cy="5" r="2" />
          <path d="M12 7v4" />
          <line x1="8" y1="16" x2="8" y2="16" />
          <line x1="16" y1="16" x2="16" y2="16" />
        </svg>
      {/if}
    </button>
  {/each}

  <div class="nav-rail-spacer"></div>

  <button
    class="nav-rail-item decisions-toggle"
    class:panel-open={panelOpen}
    aria-label="Toggle decisions panel"
    onclick={onToggleDecisions}
  >
    <div class="icon-wrapper">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
        <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
      </svg>
      {#if $decisionCount > 0}
        <span class="badge">{$decisionCount > 9 ? '9+' : $decisionCount}</span>
      {/if}
    </div>
  </button>
</nav>

<style>
  .nav-rail {
    width: 48px;
    flex: 0 0 48px;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0.5rem 0;
    gap: 0.25rem;
    border-right: 1px solid hsl(var(--border));
    background: hsl(var(--background));
  }

  .nav-rail-item {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 0.375rem;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    position: relative;
    transition: color 0.15s, background 0.15s;
  }

  .nav-rail-item:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .nav-rail-item.active {
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.1);
  }

  .nav-rail-item.active::before {
    content: '';
    position: absolute;
    left: -6px;
    top: 50%;
    transform: translateY(-50%);
    width: 3px;
    height: 20px;
    border-radius: 0 2px 2px 0;
    background: hsl(var(--primary));
  }

  .nav-rail-item svg {
    width: 18px;
    height: 18px;
  }

  .nav-rail-spacer {
    flex: 1;
  }

  .decisions-toggle.panel-open {
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.1);
    box-shadow: inset 0 0 0 1.5px hsl(var(--primary) / 0.3);
  }

  .icon-wrapper {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .badge {
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

  @media (max-width: 1024px) {
    .nav-rail {
      width: 40px;
      flex: 0 0 40px;
    }
  }

  @media (max-width: 768px) {
    .nav-rail {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      width: 100%;
      height: 48px;
      flex-direction: row;
      justify-content: space-around;
      border-right: none;
      border-top: 1px solid hsl(var(--border));
      padding: 0;
      z-index: 100;
    }

    .nav-rail-spacer {
      display: none;
    }

    .nav-rail-item.active::before {
      left: 50%;
      top: 0;
      transform: translateX(-50%);
      width: 20px;
      height: 3px;
      border-radius: 0 0 2px 2px;
    }
  }
</style>
