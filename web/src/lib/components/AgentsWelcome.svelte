<script lang="ts">
  import { router } from '../router';
  import Button from './ui/button.svelte';
</script>

<div class="agents-welcome scroll-thin">
  <div class="welcome-content">
    <div class="welcome-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="10" rx="2" />
        <circle cx="12" cy="5" r="2" />
        <path d="M12 7v4" />
        <line x1="8" y1="16" x2="8" y2="16" />
        <line x1="16" y1="16" x2="16" y2="16" />
      </svg>
    </div>
    <h3 class="welcome-title">Agents</h3>
    <p class="welcome-description">Select an agent to view details and configuration, or add a new one.</p>

    <Button variant="default" size="sm" onclick={() => router.navigate('/agents/new')}>
      Add Agent
    </Button>
  </div>
</div>

<style>
  .agents-welcome {
    flex: 1;
    overflow-y: auto;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .welcome-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 3rem 2rem;
    max-width: 28rem;
    text-align: center;
  }

  .welcome-icon {
    width: 48px;
    height: 48px;
    color: hsl(var(--muted-foreground));
    opacity: 0.5;
  }

  .welcome-icon svg {
    width: 100%;
    height: 100%;
  }

  .welcome-title {
    font-size: 1rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .welcome-description {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    line-height: 1.5;
  }
</style>
