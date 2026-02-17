<script lang="ts">
  import { currentScreen, selectedExecutionId } from '../stores/appState';
  import { executions } from '../stores/executions';
  import AppHeader from './AppHeader.svelte';
  import SplitPanel from './SplitPanel.svelte';
  import ExecutionList from './ExecutionList.svelte';
  import ExecutionDetail from './ExecutionDetail.svelte';
  import EmptyState from './EmptyState.svelte';
  import DecisionQueue from './DecisionQueue.svelte';
  import ActivityFeed from './ActivityFeed.svelte';
  import NewExecutionModal from './NewExecutionModal.svelte';

  let showNewModal = $state(false);

  let hasExecutions = $derived($executions.length > 0);
</script>

<AppHeader onnewexecution={() => showNewModal = true} />

<div class="shell-body">
  <SplitPanel storageKey="beacon-sidebar-width" initialLeftWidth={22} minWidth={15} maxWidth={40}>
    {#snippet left()}
      <div class="sidebar">
        <ExecutionList />
      </div>
    {/snippet}
    {#snippet right()}
      <div class="main-content">
        {#if $currentScreen === 'ExecutionDetail' && $selectedExecutionId}
          <ExecutionDetail executionId={$selectedExecutionId} />
        {:else if hasExecutions}
          <div class="home-view scroll-thin">
            <DecisionQueue />
            <ActivityFeed />
          </div>
        {:else}
          <EmptyState />
        {/if}
      </div>
    {/snippet}
  </SplitPanel>
</div>

{#if showNewModal}
  <NewExecutionModal onclose={() => showNewModal = false} />
{/if}

<style>
  .shell-body {
    flex: 1;
    display: flex;
    min-height: 0;
  }

  .sidebar {
    height: 100%;
    overflow-y: auto;
    border-right: 1px solid hsl(var(--border));
  }

  .main-content {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .home-view {
    flex: 1;
    overflow-y: auto;
  }
</style>
