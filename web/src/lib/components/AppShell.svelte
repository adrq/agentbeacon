<script lang="ts">
  import { get } from 'svelte/store';
  import { activeSection, sidebarOpen, selectedExecutionId, selectedProjectId, selectedAgentId, actionPanelCollapsed, userExplicitlyCollapsed, homeFeedFilter } from '../stores/appState';
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
  import OpsSummary from './OpsSummary.svelte';
  import ActionPanel from './ActionPanel.svelte';
  import NewExecutionModal from './NewExecutionModal.svelte';
  import ProjectList from './ProjectList.svelte';
  import ProjectDetail from './ProjectDetail.svelte';
  import AgentList from './AgentList.svelte';
  import AgentDetail from './AgentDetail.svelte';
  import AgentsWelcome from './AgentsWelcome.svelte';
  import ProjectsWelcome from './ProjectsWelcome.svelte';
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

  let isHome = $derived($activeSection === 'home');
  let effectiveCollapsed = $derived(isHome ? false : (isTablet || $actionPanelCollapsed));

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
    if (isTablet || isHome) return;
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

  // Reset home feed filter when navigating away from Home and Executions
  $effect(() => {
    const s = $activeSection;
    if (s !== 'home' && s !== 'executions') {
      homeFeedFilter.set(null);
    }
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
  <SplitPanel storageKey="agentbeacon-main-split" initialLeftWidth={22} minWidth={15} maxWidth={35} collapsed={!$sidebarOpen}>
    {#snippet left()}
      <div class="sidebar">
        <div class="sidebar-panel" class:hidden={$activeSection !== 'executions'}>
          <ExecutionList />
        </div>
        <div class="sidebar-panel" class:hidden={$activeSection !== 'projects'}>
          <ProjectList />
        </div>
        <div class="sidebar-panel" class:hidden={$activeSection !== 'agents'}>
          <AgentList />
        </div>
      </div>
    {/snippet}
    {#snippet right()}
      <div class="main-content" id="main-content" tabindex="-1">
        {#if $activeSection === 'home'}
          {#if hasExecutions}
            <div class="home-view scroll-thin">
              <OpsSummary executions={execsQuery.data ?? []} />
              <ActivityFeed executions={execsQuery.data ?? []} />
            </div>
          {:else}
            <EmptyState />
          {/if}
        {:else if $activeSection === 'executions'}
          {#if $selectedExecutionId}
            <ExecutionDetail executionId={$selectedExecutionId} onrerun={handleRerun} />
          {:else if hasExecutions}
            <div class="home-view scroll-thin">
              <OpsSummary executions={execsQuery.data ?? []} />
              <ActivityFeed executions={execsQuery.data ?? []} />
            </div>
          {:else}
            <EmptyState />
          {/if}
        {:else if $activeSection === 'projects'}
          {#if $selectedProjectId}
            <ProjectDetail projectId={$selectedProjectId} />
          {:else}
            <ProjectsWelcome />
          {/if}
        {:else if $activeSection === 'agents'}
          {#if $selectedAgentId}
            <AgentDetail agentId={$selectedAgentId} />
          {:else}
            <AgentsWelcome />
          {/if}
        {/if}
      </div>
    {/snippet}
  </SplitPanel>
  <ActionPanel
    collapsed={effectiveCollapsed}
    onToggle={toggleActionPanel}
    decisionCount={$decisionCount}
    wide={isHome}
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

  .sidebar-panel {
    height: 100%;
    overflow-y: auto;
  }

  .sidebar-panel.hidden {
    display: none;
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
    border-radius: 0 0 var(--radius) 0;
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
