<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { theme as themeStore, currentScreen, routeParams } from './lib/stores/appState';
  import { router } from './lib/router';
  import type { Theme } from './lib/types';
  import AppHeader from './lib/components/AppHeader.svelte';
  import Dashboard from './routes/Dashboard.svelte';
  import TemplateGallery from './routes/TemplateGallery.svelte';
  import WorkflowEditorScreen from './routes/WorkflowEditorScreen.svelte';
  import RunDetails from './routes/RunDetails.svelte';

  let theme: Theme;
  let unsubscribeTheme: (() => void) | undefined;
  let unsubscribeRouter: (() => void) | undefined;

  onMount(() => {
    // Subscribe to theme store
    unsubscribeTheme = themeStore.subscribe(value => {
      theme = value;
    });

    // Subscribe to router changes and update currentScreen store
    unsubscribeRouter = router.onRouteChange((route) => {
      currentScreen.set(route.screen);
      routeParams.set(route.params);
    });

    // Initialize route from current URL
    const initialRoute = router.getCurrentRoute();
    currentScreen.set(initialRoute.screen);
    routeParams.set(initialRoute.params);
  });

  onDestroy(() => {
    if (unsubscribeTheme) unsubscribeTheme();
    if (unsubscribeRouter) unsubscribeRouter();
  });

  function handleThemeChange(event: CustomEvent<Theme>) {
    themeStore.set(event.detail);
  }

  // Navigation handlers - Dashboard
  function handleDashboardNavigateToTemplateGallery() {
    router.navigate('/templates');
  }

  function handleDashboardNavigateToWorkflowEditor(event: CustomEvent<{ workflowId: string }>) {
    // Always navigate to hard-coded placeholder workflow ID
    router.navigate('/editor/workflow-demo');
  }

  function handleDashboardNavigateToRunDetails(event: CustomEvent<{ runId: string }>) {
    router.navigate(`/run/${event.detail.runId}`);
  }

  // Navigation handlers - TemplateGallery
  function handleTemplateGalleryNavigateToDashboard() {
    router.navigate('/');
  }

  function handleTemplateGallerySelectTemplate(event: CustomEvent<{ templateId: string }>) {
    // Always navigate to hard-coded placeholder workflow ID
    router.navigate('/editor/workflow-demo');
  }

  // Navigation handlers - WorkflowEditorScreen
  function handleWorkflowEditorNavigateToDashboard() {
    router.navigate('/');
  }

  function handleWorkflowEditorNavigateToRunDetails(event: CustomEvent<{ runId: string }>) {
    // Always navigate to hard-coded placeholder run ID
    router.navigate('/run/run-demo');
  }

  // Navigation handlers - RunDetails
  function handleRunDetailsNavigateToWorkflowEditor(event: CustomEvent<{ workflowId: string }>) {
    // Always navigate to hard-coded placeholder workflow ID
    router.navigate('/editor/workflow-demo');
  }
</script>

<div class="app-shell" class:dark={theme === 'dark'}>
  <AppHeader {theme} on:themeChange={handleThemeChange} />

  <main class="app-main">
    {#if $currentScreen === 'Dashboard'}
      <Dashboard
        {theme}
        on:navigateToTemplateGallery={handleDashboardNavigateToTemplateGallery}
        on:navigateToWorkflowEditor={handleDashboardNavigateToWorkflowEditor}
        on:navigateToRunDetails={handleDashboardNavigateToRunDetails}
      />
    {:else if $currentScreen === 'TemplateGallery'}
      <TemplateGallery
        {theme}
        on:navigateToDashboard={handleTemplateGalleryNavigateToDashboard}
        on:selectTemplate={handleTemplateGallerySelectTemplate}
      />
    {:else if $currentScreen === 'WorkflowEditor'}
      <WorkflowEditorScreen
        {theme}
        params={$routeParams}
        on:navigateToDashboard={handleWorkflowEditorNavigateToDashboard}
        on:navigateToRunDetails={handleWorkflowEditorNavigateToRunDetails}
      />
    {:else if $currentScreen === 'RunDetails'}
      <RunDetails
        {theme}
        params={$routeParams}
        on:navigateToWorkflowEditor={handleRunDetailsNavigateToWorkflowEditor}
      />
    {/if}
  </main>
</div>

<style>
  .app-shell {
    width: 100%;
    height: 100vh;
    display: flex;
    flex-direction: column;
    background: #f8fafc;
    color: #0f172a;
    overflow: hidden;
  }

  .app-shell.dark {
    background: #0f172a;
    color: #e2e8f0;
  }

  .app-main {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
</style>
