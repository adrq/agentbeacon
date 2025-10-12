<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Theme, BreadcrumbSegment } from '../lib/types';
  import ScreenHeader from '../lib/components/ScreenHeader.svelte';
  import TemplateCardComponent from '../lib/components/TemplateCardComponent.svelte';
  import { templateCards } from '../lib/stores/placeholderData';

  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    navigateToDashboard: void;
    selectTemplate: { templateId: string };
  }>();

  const breadcrumbs: BreadcrumbSegment[] = [
    { label: '🎭 AgentMaestro', path: '/' },
    { label: 'New Workflow', path: '/templates' }
  ];

  function handleBackToDashboard() {
    dispatch('navigateToDashboard');
  }

  function handleSelectTemplate(templateId: string) {
    dispatch('selectTemplate', { templateId });
  }

  function handleBreadcrumbNavigate(event: CustomEvent<{ path: string }>) {
    if (event.detail.path === '/') {
      dispatch('navigateToDashboard');
    }
  }

  function handleStartFromScratch() {
    // Navigate to editor with empty workflow
    dispatch('selectTemplate', { templateId: 'scratch' });
  }
</script>

<div class="template-gallery" class:dark={theme === 'dark'}>
  <ScreenHeader
    breadcrumbSegments={breadcrumbs}
    {theme}
    on:navigate={handleBreadcrumbNavigate}
  >
    <div slot="actions" class="header-actions">
      <button class="btn-dashboard" on:click={handleBackToDashboard}>
        Dashboard
      </button>
    </div>
  </ScreenHeader>

  <div class="gallery-content">
    <h1 class="page-title">Choose a Template</h1>

    <section class="templates-section">
      <h2 class="section-heading">🎯 Popular Templates</h2>
      <div class="template-grid">
        {#each templateCards as template (template.id)}
          <TemplateCardComponent
            {template}
            {theme}
            on:use={() => handleSelectTemplate(template.id)}
          />
        {/each}
      </div>
    </section>

    <section class="advanced-section">
      <h2 class="section-heading">🛠️ Advanced</h2>
      <button class="scratch-option" on:click={handleStartFromScratch}>
        <div class="scratch-content">
          <span class="scratch-icon">📝</span>
          <div class="scratch-text">
            <div class="scratch-title">Start from Scratch</div>
            <div class="scratch-description">Write custom YAML</div>
          </div>
        </div>
      </button>
    </section>
  </div>
</div>

<style>
  .template-gallery {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #f8fafc;
    overflow: auto;
  }

  .template-gallery.dark {
    background: #0f172a;
  }

  .gallery-content {
    flex: 1;
    padding: 2rem;
    max-width: 1400px;
    margin: 0 auto;
    width: 100%;
  }

  .header-actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
  }

  .btn-dashboard {
    padding: 0.5rem 1rem;
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-dashboard:hover {
    background: #e2e8f0;
    border-color: #94a3b8;
  }

  .template-gallery.dark .btn-dashboard {
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
  }

  .template-gallery.dark .btn-dashboard:hover {
    background: #475569;
    border-color: #64748b;
  }

  .page-title {
    margin: 0 0 2rem 0;
    font-size: 1.875rem;
    font-weight: 700;
    color: #0f172a;
  }

  .template-gallery.dark .page-title {
    color: #e2e8f0;
  }

  .templates-section {
    margin-bottom: 3rem;
  }

  .section-heading {
    margin: 0 0 1.5rem 0;
    font-size: 1.25rem;
    font-weight: 600;
    color: #0f172a;
  }

  .template-gallery.dark .section-heading {
    color: #e2e8f0;
  }

  .template-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1.5rem;
  }

  @media (max-width: 1023px) {
    .template-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (max-width: 767px) {
    .template-grid {
      grid-template-columns: 1fr;
    }
  }

  .advanced-section {
    margin-bottom: 2rem;
  }

  .scratch-option {
    width: 100%;
    max-width: 600px;
    padding: 1.5rem;
    background: #ffffff;
    border: 2px dashed #cbd5e1;
    border-radius: 0.5rem;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .scratch-option:hover {
    border-color: #3b82f6;
    background: #f8fafc;
  }

  .template-gallery.dark .scratch-option {
    background: #1e293b;
    border-color: #475569;
  }

  .template-gallery.dark .scratch-option:hover {
    border-color: #60a5fa;
    background: #334155;
  }

  .scratch-content {
    display: flex;
    align-items: center;
    gap: 1rem;
  }

  .scratch-icon {
    font-size: 2rem;
  }

  .scratch-text {
    text-align: left;
  }

  .scratch-title {
    font-size: 1.125rem;
    font-weight: 600;
    color: #0f172a;
    margin-bottom: 0.25rem;
  }

  .template-gallery.dark .scratch-title {
    color: #e2e8f0;
  }

  .scratch-description {
    font-size: 0.875rem;
    color: #64748b;
  }

  .template-gallery.dark .scratch-description {
    color: #94a3b8;
  }

  @media (max-width: 480px) {
    .gallery-content {
      padding: 1rem;
    }

    .page-title {
      font-size: 1.5rem;
    }
  }
</style>
