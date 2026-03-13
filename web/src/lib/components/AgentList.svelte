<script lang="ts">
  import { selectedAgentId } from '../stores/appState';
  import { agentsQuery } from '../queries/agents';
  import { router } from '../router';
  import { typeLabels } from '../utils/agentUtils';

  const agents = agentsQuery();

  let searchText = $state('');

  let filtered = $derived(
    searchText.trim()
      ? (agents.data ?? []).filter(a => {
          const q = searchText.toLowerCase();
          return a.name.toLowerCase().includes(q)
            || (typeLabels[a.agent_type] ?? a.agent_type).toLowerCase().includes(q)
            || (a.description?.toLowerCase().includes(q) ?? false);
        })
      : (agents.data ?? [])
  );
</script>

<div class="agent-list">
  <div class="list-header">
    <div class="search-bar">
      <div class="search-input-wrapper">
        <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          class="search-input"
          type="text"
          placeholder="Search agents..."
          aria-label="Search agents"
          bind:value={searchText}
        />
        {#if searchText}
          <button class="search-clear" onclick={() => searchText = ''} aria-label="Clear search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        {/if}
      </div>
    </div>
    <div class="header-action">
      <button class="add-btn" onclick={() => router.navigate('/agents/new')} aria-label="Add agent">+ Add</button>
    </div>
  </div>

  {#if agents.isLoading}
    <div class="list-message">Loading...</div>
  {:else if agents.isError}
    <div class="list-message list-error">{agents.error?.message ?? 'Failed to load'}</div>
  {:else if filtered.length === 0}
    <div class="list-message">{searchText ? 'No matches' : 'No agents yet'}</div>
  {:else}
    {#each filtered as agent (agent.id)}
      <button
        class="list-item"
        class:selected={$selectedAgentId === agent.id}
        class:disabled={!agent.enabled}
        onclick={() => router.navigate(`/agents/${agent.id}`)}
      >
        <div class="item-top">
          <span class="item-name">{agent.name}</span>
          <span class="type-badge">{typeLabels[agent.agent_type] ?? agent.agent_type}</span>
          {#if !agent.enabled}
            <span class="disabled-badge">off</span>
          {/if}
        </div>
        {#if agent.description}
          <div class="item-desc">{agent.description}</div>
        {/if}
      </button>
    {/each}
  {/if}
</div>

<style>
  .agent-list {
    height: 100%;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .list-header {
    border-bottom: 1px solid hsl(var(--border));
  }

  .search-bar {
    padding: 0.5rem;
  }

  .search-input-wrapper {
    position: relative;
    display: flex;
    align-items: center;
  }

  .search-icon {
    position: absolute;
    left: 0.5rem;
    width: 14px;
    height: 14px;
    color: hsl(var(--muted-foreground));
    pointer-events: none;
  }

  .search-input {
    width: 100%;
    padding: 0.375rem 0.5rem 0.375rem 1.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
    font-family: inherit;
  }

  .search-input::placeholder {
    color: hsl(var(--muted-foreground));
  }

  .search-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .search-clear {
    position: absolute;
    right: 0.25rem;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: var(--radius-sm);
  }

  .search-clear:hover {
    color: hsl(var(--foreground));
  }

  .search-clear svg {
    width: 12px;
    height: 12px;
  }

  .header-action {
    padding: 0 0.5rem 0.5rem;
  }

  .add-btn {
    width: 100%;
    padding: 0.25rem 0.5rem;
    font-size: 0.6875rem;
    font-weight: 500;
    border: 1px dashed hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }

  .add-btn:hover {
    border-color: hsl(var(--primary));
    color: hsl(var(--primary));
  }

  .list-item {
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
    padding: 0.5rem 0.75rem;
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    text-align: left;
    cursor: pointer;
    width: 100%;
    transition: background 0.1s;
  }

  .list-item:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .list-item.selected {
    border-left-color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.08);
  }

  .list-item.disabled {
    opacity: 0.6;
  }

  .item-top {
    display: flex;
    align-items: center;
    gap: 0.375rem;
  }

  .item-name {
    font-size: 0.8125rem;
    font-weight: 500;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .type-badge {
    font-size: 0.5625rem;
    font-weight: 600;
    padding: 0 0.25rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }

  .disabled-badge {
    font-size: 0.5625rem;
    font-weight: 500;
    padding: 0 0.25rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    flex-shrink: 0;
  }

  .item-desc {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .list-message {
    padding: 2rem 1rem;
    text-align: center;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .list-error {
    color: hsl(var(--status-danger));
  }
</style>
