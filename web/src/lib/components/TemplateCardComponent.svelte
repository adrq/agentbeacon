<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { TemplateCard, Theme } from '../types';

  export let template: TemplateCard;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ use: void }>();
</script>

<div class="template-card" class:dark={theme === 'dark'}>
  <div class="template-icon">{template.icon}</div>
  <h3 class="template-name">{template.name}</h3>
  <div class="template-usage">
    <span>⭐ {template.usageCount} uses</span>
  </div>
  <p class="template-description">{template.description}</p>
  <button class="btn-use" on:click={() => dispatch('use')}>Use</button>
</div>

<style>
  .template-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    transition: all 0.2s ease;
  }

  .template-card:hover {
    border-color: #3b82f6;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    transform: translateY(-2px);
  }

  .template-card.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .template-card.dark:hover {
    border-color: #60a5fa;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  }

  .template-icon {
    font-size: 3rem;
    margin-bottom: 0.75rem;
  }

  .template-name {
    margin: 0 0 0.5rem 0;
    font-size: 1.125rem;
    font-weight: 600;
    color: #0f172a;
  }

  .dark .template-name {
    color: #e2e8f0;
  }

  .template-usage {
    font-size: 0.875rem;
    color: #64748b;
    margin-bottom: 0.75rem;
  }

  .dark .template-usage {
    color: #94a3b8;
  }

  .template-description {
    margin: 0 0 1rem 0;
    font-size: 0.875rem;
    color: #64748b;
    line-height: 1.5;
    flex: 1;
  }

  .dark .template-description {
    color: #94a3b8;
  }

  .btn-use {
    width: 100%;
    padding: 0.625rem 1.25rem;
    background: #3b82f6;
    color: #ffffff;
    border: none;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.2s ease;
  }

  .btn-use:hover {
    background: #2563eb;
  }
</style>
