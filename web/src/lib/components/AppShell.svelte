<script lang="ts">
  import { get } from 'svelte/store';
  import { currentScreen, selectedExecutionId, selectedProjectId, actionPanelCollapsed, userExplicitlyCollapsed } from '../stores/appState';
  import { decisionCount } from '../stores/questionState';
  import { executionsQuery } from '../queries/executions';
  import AppHeader from './AppHeader.svelte';
  import NavRail from './NavRail.svelte';
  import SplitPanel from './SplitPanel.svelte';
  import ExecutionList from './ExecutionList.svelte';
  import ExecutionDetail from './ExecutionDetail.svelte';
  import type { ExecutionPrefill } from './ExecutionDetail.svelte';
  import EmptyState from './EmptyState.svelte';
  import ActivityFeed from './ActivityFeed.svelte';
  import ActionPanel from './ActionPanel.svelte';
  import NewExecutionModal from './NewExecutionModal.svelte';
  import ProjectsView from './ProjectsView.svelte';
  import ProjectDetail from './ProjectDetail.svelte';
  import AgentsView from './AgentsView.svelte';
  import QuestionStateProvider from './QuestionStateProvider.svelte';

  let showNewModal = $state(false);
  let rerunPrefill = $state<ExecutionPrefill | null>(null);

  // Track tablet breakpoint so we can force-collapse ActionPanel via state (not CSS)
  let isTablet = $state(false);
  $effect(() => {
    const mql = window.matchMedia('(max-width: 1024px)');
    isTablet = mql.matches;
    const handler = (e: MediaQueryListEvent) => { isTablet = e.matches; };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  });

  let effectiveCollapsed = $derived(isTablet || $actionPanelCollapsed);

  const execsQuery = executionsQuery();
  let hasExecutions = $derived((execsQuery.data ?? []).length > 0);

  function handleNewExecution() {
    rerunPrefill = null;
    showNewModal = true;
  }

  function handleRerun(prefill: ExecutionPrefill) {
    rerunPrefill = prefill;
    showNewModal = true;
  }

  function handleModalClose() {
    showNewModal = false;
    rerunPrefill = null;
  }

  function toggleActionPanel() {
    if (isTablet) return; // No-op: panel is force-collapsed at tablet width
    actionPanelCollapsed.update(v => {
      const next = !v;
      if (next) {
        userExplicitlyCollapsed.set(true);
      }
      return next;
    });
  }

  // Auto-expand when decisions go from 0 to >0, unless user explicitly collapsed.
  // Auto-collapse only on transition from >0 to 0 (not every tick at 0).
  let prevDecisionCount = 0;
  $effect(() => {
    const count = $decisionCount;
    const explicit = get(userExplicitlyCollapsed);
    const collapsed = get(actionPanelCollapsed);

    if (count > 0 && !explicit && collapsed && !isTablet) {
      actionPanelCollapsed.set(false);
    }
    if (count === 0 && prevDecisionCount > 0) {
      userExplicitlyCollapsed.set(false);
      actionPanelCollapsed.set(true);
    }
    prevDecisionCount = count;
  });

  // Page title: "(N) AgentBeacon" when decisions are pending, like Slack's unread count
  $effect(() => {
    const count = $decisionCount;
    document.title = count > 0 ? `(${count}) AgentBeacon` : 'AgentBeacon';
  });
</script>

<QuestionStateProvider />

<a
  href="#main-content"
  class="skip-link"
  onclick={(e: MouseEvent) => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}
>Skip to main content</a>

<AppHeader onnewexecution={handleNewExecution} />

<div class="shell-body">
  <NavRail onToggleDecisions={toggleActionPanel} panelOpen={!effectiveCollapsed} />
  <SplitPanel storageKey="agentbeacon-main-split" initialLeftWidth={22} minWidth={15} maxWidth={35}>
    {#snippet left()}
      <div class="sidebar">
        <ExecutionList />
      </div>
    {/snippet}
    {#snippet right()}
      <div class="main-content" id="main-content" tabindex="-1">
        {#if $currentScreen === 'ExecutionDetail' && $selectedExecutionId}
          <ExecutionDetail executionId={$selectedExecutionId} onrerun={handleRerun} />
        {:else if $currentScreen === 'Projects'}
          <ProjectsView />
        {:else if $currentScreen === 'ProjectDetail' && $selectedProjectId}
          <ProjectDetail projectId={$selectedProjectId} />
        {:else if $currentScreen === 'Agents'}
          <AgentsView />
        {:else if hasExecutions}
          <div class="home-view scroll-thin">
            <ActivityFeed />
          </div>
        {:else}
          <EmptyState />
        {/if}
      </div>
    {/snippet}
  </SplitPanel>
  <ActionPanel
    collapsed={effectiveCollapsed}
    onToggle={toggleActionPanel}
    decisionCount={$decisionCount}
  />
</div>

{#if showNewModal}
  <NewExecutionModal onclose={handleModalClose} prefill={rerunPrefill} />
{/if}

<style>
  .shell-body {
    flex: 1;
    display: flex;
    min-height: 0;
    overflow: hidden;
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

  .skip-link {
    position: absolute;
    left: -9999px;
    top: 0;
    z-index: 200;
    padding: 0.5rem 1rem;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    font-size: 0.8125rem;
    border-radius: 0 0 0.375rem 0;
    text-decoration: none;
  }

  .skip-link:focus {
    left: 0;
  }

  @media (max-width: 768px) {
    .shell-body {
      padding-bottom: 48px;
    }
  }
</style>
