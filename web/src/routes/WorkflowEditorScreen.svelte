<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Theme, RouteParams, BreadcrumbSegment } from '../lib/types';
  import ScreenHeader from '../lib/components/ScreenHeader.svelte';
  import TabNavigation from '../lib/components/TabNavigation.svelte';
  import SplitPanel from '../lib/components/SplitPanel.svelte';
  import WorkflowEditor from '../lib/components/WorkflowEditor.svelte';
  import DAGVisualization from '../lib/components/DAGVisualization.svelte';
  import CollapsibleSection from '../lib/components/CollapsibleSection.svelte';
  import RunEntryComponent from '../lib/components/RunEntryComponent.svelte';
  import VersionEntryComponent from '../lib/components/VersionEntryComponent.svelte';
  import { sampleYAML, runEntries, versionEntries } from '../lib/stores/placeholderData';

  export let theme: Theme;
  export let params: RouteParams;

  const dispatch = createEventDispatcher<{
    navigateToDashboard: void;
    navigateToRunDetails: { runId: string };
  }>();

  let activeTabIndex = 0;
  let yamlContent = sampleYAML;
  let validationExpanded = false;

  const tabs = ['📝 Definition', '📊 Runs (7)', '📜 Versions (3)'];

  const breadcrumbs: BreadcrumbSegment[] = [
    { label: '🎭 Fix Frontend Tests v1.2.0', path: '/' }
  ];

  function handleTabChange(event: CustomEvent<{ index: number; label: string }>) {
    activeTabIndex = event.detail.index;
  }

  function handleBack() {
    dispatch('navigateToDashboard');
  }

  function handleBreadcrumbNavigate(event: CustomEvent<{ path: string }>) {
    dispatch('navigateToDashboard');
  }

  function handleYamlChange(event: CustomEvent<string>) {
    yamlContent = event.detail;
  }

  function handleRunViewDetails(runId: string) {
    dispatch('navigateToRunDetails', { runId });
  }

  // Passive button handlers (do nothing)
  function handleSave() {
    console.log('Save clicked (passive)');
  }

  function handleRun() {
    console.log('Run clicked (passive)');
  }

  function handleSettings() {
    console.log('Settings clicked (passive)');
  }
</script>

<div class="workflow-editor-screen" class:dark={theme === 'dark'}>
  <ScreenHeader
    breadcrumbSegments={breadcrumbs}
    {theme}
    on:navigate={handleBreadcrumbNavigate}
  >
    <div slot="actions" class="header-actions">
      <button class="btn-action" on:click={handleBack}>
        ← Back
      </button>
      <button class="btn-action" on:click={handleSave}>
        💾 Save
      </button>
      <button class="btn-action" on:click={handleRun}>
        ▶️ Run▼
      </button>
      <button class="btn-action" on:click={handleSettings}>
        ⚙️
      </button>
    </div>
  </ScreenHeader>

  <div class="editor-content">
    <TabNavigation
      {tabs}
      {activeTabIndex}
      {theme}
      on:tabChange={handleTabChange}
    />

    <div class="tab-content">
      {#if activeTabIndex === 0}
        <!-- Definition Tab -->
        <div class="definition-tab">
          <SplitPanel storageKey="workflow-editor-split" initialLeftWidth={60}>
            <div slot="left" class="panel-content">
              <WorkflowEditor
                value={yamlContent}
                {theme}
                readOnly={false}
                on:change={handleYamlChange}
              />
            </div>
            <div slot="right" class="panel-content">
              <DAGVisualization
                workflow={yamlContent}
                isValid={true}
                {theme}
                placeholderMode={false}
              />
            </div>
          </SplitPanel>

          <div class="validation-section">
            <CollapsibleSection
              title="▼ Validation Errors"
              expanded={validationExpanded}
              {theme}
              on:toggle={(e) => validationExpanded = e.detail.expanded}
            >
              <div class="validation-content">
                <p class="no-errors">No validation errors</p>
              </div>
            </CollapsibleSection>
          </div>
        </div>

      {:else if activeTabIndex === 1}
        <!-- Runs Tab -->
        <div class="runs-tab">
          <div class="runs-list">
            {#each runEntries as run (run.runNumber)}
              <RunEntryComponent
                {run}
                {theme}
                on:viewDetails={() => handleRunViewDetails(`run-${run.runNumber}`)}
                on:stop={() => console.log('Stop run:', run.runNumber)}
                on:rerun={() => console.log('Rerun:', run.runNumber)}
                on:compare={() => console.log('Compare:', run.runNumber)}
                on:debug={() => console.log('Debug:', run.runNumber)}
              />
            {/each}
          </div>
        </div>

      {:else if activeTabIndex === 2}
        <!-- Versions Tab -->
        <div class="versions-tab">
          <div class="versions-list">
            {#each versionEntries as version (version.version)}
              <VersionEntryComponent
                {version}
                {theme}
                on:view={() => console.log('View version:', version.version)}
                on:run={() => console.log('Run version:', version.version)}
              />
            {/each}
          </div>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .workflow-editor-screen {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #f8fafc;
  }

  .workflow-editor-screen.dark {
    background: #0f172a;
  }

  .header-actions {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .btn-action {
    padding: 0.5rem 0.875rem;
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-action:hover {
    background: #e2e8f0;
    border-color: #94a3b8;
  }

  .workflow-editor-screen.dark .btn-action {
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
  }

  .workflow-editor-screen.dark .btn-action:hover {
    background: #475569;
    border-color: #64748b;
  }

  .editor-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .tab-content {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .definition-tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-content {
    height: 100%;
    overflow: auto;
  }

  .validation-section {
    border-top: 1px solid #e2e8f0;
  }

  .workflow-editor-screen.dark .validation-section {
    border-top-color: #334155;
  }

  .validation-content {
    padding: 1rem;
  }

  .no-errors {
    margin: 0;
    color: #059669;
    font-size: 0.875rem;
    font-style: italic;
  }

  .workflow-editor-screen.dark .no-errors {
    color: #6ee7b7;
  }

  .runs-tab,
  .versions-tab {
    flex: 1;
    overflow: auto;
    padding: 1.5rem;
  }

  .runs-list,
  .versions-list {
    max-width: 1200px;
    margin: 0 auto;
  }

  @media (max-width: 768px) {
    .header-actions {
      gap: 0.25rem;
    }

    .btn-action {
      padding: 0.5rem;
      font-size: 0.75rem;
    }

    .runs-tab,
    .versions-tab {
      padding: 1rem;
    }
  }

  @media (max-width: 480px) {
    .btn-action {
      min-width: 2.5rem;
      padding: 0.5rem 0.25rem;
    }
  }
</style>
