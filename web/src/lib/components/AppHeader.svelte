<script lang="ts">
  import ThemeToggle from './ThemeToggle.svelte';
  import Button from './ui/button.svelte';
  import { theme } from '../stores/appState';
  import { router } from '../router';
  import logoDarkUrl from '../../assets/logo-mark-dark.svg';
  import logoLightUrl from '../../assets/logo-mark-light.svg';

  let logoUrl = $derived($theme === 'light' ? logoLightUrl : logoDarkUrl);
</script>

<header class="app-header">
  <div class="header-left">
    <a href="#/" class="brand">
      <img src={logoUrl} alt="" class="beacon-icon" width="24" height="24" />
      <span class="app-name">AgentBeacon</span>
    </a>
  </div>
  <div class="header-right">
    <Button variant="default" size="sm" onclick={() => router.navigate('/executions/new')}>
      + New
    </Button>
    <a href="/docs/" target="_blank" rel="noopener" class="docs-link" title="Documentation">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
        <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
      </svg>
    </a>
    <ThemeToggle />
  </div>
</header>

<style>
  .app-header {
    height: 2.5rem;
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
    width: 1.5rem;
    height: 1.5rem;
  }

  .app-name {
    font-weight: 600;
    font-size: 0.875rem;
    letter-spacing: -0.01em;
    user-select: none;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .docs-link {
    display: flex;
    align-items: center;
    color: hsl(var(--muted-foreground));
    transition: color 0.15s;
  }

  .docs-link:hover {
    color: hsl(var(--foreground));
  }
</style>
