<script lang="ts">
  import { currentScreen } from '../stores/appState';
  import ThemeToggle from './ThemeToggle.svelte';
  import Button from './ui/button.svelte';

  interface Props {
    onnewexecution?: () => void;
  }

  let { onnewexecution }: Props = $props();

  const navItems: { label: string; hash: string; screens: string[] }[] = [
    { label: 'Executions', hash: '#/', screens: ['Home', 'ExecutionDetail'] },
    { label: 'Projects', hash: '#/projects', screens: ['Projects', 'ProjectDetail'] },
    { label: 'Agents', hash: '#/agents', screens: ['Agents'] },
  ];
</script>

<header class="app-header">
  <div class="header-left">
    <a href="#/" class="brand">
      <svg class="beacon-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v3" />
        <path d="M12 18v3" />
        <path d="M3 12h3" />
        <path d="M18 12h3" />
        <path d="M5.6 5.6l2.1 2.1" />
        <path d="M16.3 16.3l2.1 2.1" />
        <path d="M5.6 18.4l2.1-2.1" />
        <path d="M16.3 7.7l2.1-2.1" />
      </svg>
      <span class="app-name">AgentBeacon</span>
    </a>
    <nav class="header-nav" aria-label="Main navigation">
      {#each navItems as item}
        <a
          href={item.hash}
          class="nav-link"
          class:active={item.screens.includes($currentScreen)}
          aria-current={item.screens.includes($currentScreen) ? 'page' : undefined}
        >{item.label}</a>
      {/each}
    </nav>
  </div>
  <div class="header-right">
    <Button variant="default" size="sm" onclick={() => onnewexecution?.()}>
      + New
    </Button>
    <ThemeToggle />
  </div>
</header>

<style>
  .app-header {
    height: 3rem;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 1rem;
    border-bottom: 1px solid hsl(var(--border));
    background: hsl(var(--background));
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 1.5rem;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    text-decoration: none;
    color: inherit;
  }

  .beacon-icon {
    width: 1.25rem;
    height: 1.25rem;
    color: hsl(var(--primary));
  }

  .app-name {
    font-weight: 600;
    font-size: 1rem;
    letter-spacing: -0.01em;
    user-select: none;
  }

  .header-nav {
    display: flex;
    align-items: center;
    gap: 0.125rem;
  }

  .nav-link {
    padding: 0.25rem 0.625rem;
    font-size: 0.8125rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    text-decoration: none;
    border-radius: 0.25rem;
    transition: color 0.15s, background 0.15s;
  }

  .nav-link:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .nav-link.active {
    color: hsl(var(--foreground));
    background: hsl(var(--primary) / 0.1);
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
</style>
