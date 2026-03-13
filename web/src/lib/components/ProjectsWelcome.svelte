<script lang="ts">
  import Button from './ui/button.svelte';
  import { router } from '../router';
</script>

<div class="projects-welcome scroll-thin">
  <div class="welcome-content">
    <div class="welcome-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
      </svg>
    </div>
    <h3 class="welcome-title">Projects</h3>
    <p class="welcome-description">Select a project to view details, recent executions, and default agent configuration.</p>

    <Button variant="default" size="sm" onclick={() => router.navigate('/projects/new')}>
      Register Project
    </Button>
  </div>
</div>

<style>
  .projects-welcome {
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
