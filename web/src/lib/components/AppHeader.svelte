<script lang="ts">
  import ThemeToggle from './ThemeToggle.svelte';
  import Button from './ui/button.svelte';
  import { theme } from '../stores/appState';
  import logoDarkUrl from '../../assets/logo-mark-dark.svg';
  import logoLightUrl from '../../assets/logo-mark-light.svg';

  interface Props {
    onnewexecution?: () => void;
  }

  let { onnewexecution }: Props = $props();

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
    <Button variant="default" size="sm" onclick={() => onnewexecution?.()}>
      + New
    </Button>
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
</style>
