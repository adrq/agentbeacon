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
    <button class="settings-btn" title="Settings" aria-label="Settings" onclick={() => router.navigate('/settings')}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M6.5 1L6.2 2.6C5.8 2.8 5.4 3 5.1 3.3L3.5 2.7L2 5.3L3.4 6.4C3.4 6.6 3.3 6.8 3.3 7V7C3.3 7.2 3.4 7.4 3.4 7.6L2 8.7L3.5 11.3L5.1 10.7C5.4 11 5.8 11.2 6.2 11.4L6.5 13H9.5L9.8 11.4C10.2 11.2 10.6 11 10.9 10.7L12.5 11.3L14 8.7L12.6 7.6C12.6 7.4 12.7 7.2 12.7 7V7C12.7 6.8 12.6 6.6 12.6 6.4L14 5.3L12.5 2.7L10.9 3.3C10.6 3 10.2 2.8 9.8 2.6L9.5 1H6.5Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
        <circle cx="8" cy="7" r="2" stroke="currentColor" stroke-width="1.2"/>
      </svg>
    </button>
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

  .settings-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    border: none;
    border-radius: var(--radius);
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
  }

  .settings-btn:hover {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
  }
</style>
