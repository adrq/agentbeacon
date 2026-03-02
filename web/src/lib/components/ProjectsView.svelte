<script lang="ts">
  import { projectsQuery } from '../queries/projects';
  import { agentsQuery } from '../queries/agents';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import ProjectForm from './ProjectForm.svelte';

  const projects = projectsQuery();
  const agents = agentsQuery();

  let showCreateForm = $state(false);

  let agentNameMap = $derived(
    new Map((agents.data ?? []).map(a => [a.id, a.name]))
  );

  function handleProjectCreated() {
    showCreateForm = false;
  }
</script>

<div class="projects-view scroll-thin">
  <div class="view-header">
    <h2 class="view-title">Projects</h2>
    <Button variant="default" size="sm" onclick={() => showCreateForm = true}>
      Register Project
    </Button>
  </div>

  {#if projects.isLoading}
    <div class="view-message">Loading projects...</div>
  {:else if projects.isError}
    <div class="view-message view-error">{projects.error?.message ?? 'Failed to load'}</div>
  {:else if (projects.data ?? []).length === 0}
    <div class="empty-state">
      <p class="empty-title">No projects registered</p>
      <p class="empty-description">Register a project directory to start organizing your executions.</p>
      <Button variant="default" size="sm" onclick={() => showCreateForm = true}>
        Register Project
      </Button>
    </div>
  {:else}
    <div class="projects-grid">
      {#each projects.data ?? [] as project (project.id)}
        <button class="project-card" onclick={() => router.navigate(`/projects/${project.id}`)}>
          <div class="card-top">
            <span class="card-name">{project.name}</span>
            {#if project.is_git}
              <span class="git-badge">git</span>
            {/if}
          </div>
          <div class="card-path">{project.path}</div>
          {#if project.default_agent_id}
            <div class="card-agent">Default: {agentNameMap.get(project.default_agent_id) ?? 'Unknown'}</div>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if showCreateForm}
  <ProjectForm
    onsubmit={handleProjectCreated}
    oncancel={() => showCreateForm = false}
  />
{/if}

<style>
  .projects-view {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
  }

  .view-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1rem;
  }

  .view-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .view-message {
    text-align: center;
    padding: 2rem;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
  }

  .view-error {
    color: hsl(var(--status-danger));
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 3rem 2rem;
    border: 1px dashed hsl(var(--border));
    border-radius: var(--radius);
    text-align: center;
  }

  .empty-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .empty-description {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    max-width: 24rem;
  }

  .projects-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    gap: 0.75rem;
  }

  .project-card {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 1rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    text-align: left;
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
    width: 100%;
  }

  .project-card:hover {
    border-color: hsl(var(--primary) / 0.5);
    box-shadow: 0 1px 4px hsl(var(--primary) / 0.08);
  }

  .card-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .card-name {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .git-badge {
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .card-path {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .card-agent {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
  }
</style>
