<script lang="ts">
  import { onMount } from 'svelte';
  import { Tabs } from 'bits-ui';
  import { getWikiTabs, getActiveTabId, setActiveTab, closeTab, openSearchTab, openPage, updateTabProjectName } from '../../stores/wikiState.svelte';
  import { projectsQuery } from '../../queries/projects';
  import { router } from '../../router';
  import type { RouteState } from '../../router';
  import WikiSearchTab from './WikiSearchTab.svelte';
  import WikiPageView from './WikiPageView.svelte';

  const projects = projectsQuery();
  let tabs = $derived(getWikiTabs());
  let activeId = $derived(getActiveTabId());

  function projectName(id: string): string | undefined {
    return (projects.data ?? []).find(p => p.id === id)?.name;
  }

  // Hash a string to a hue (0-360) for per-project badge colors
  function projectHue(id: string): number {
    let hash = 0;
    for (let i = 0; i < id.length; i++) {
      hash = ((hash << 5) - hash + id.charCodeAt(i)) | 0;
    }
    return ((hash % 360) + 360) % 360;
  }

  function handleValueChange(value: string) {
    setActiveTab(value);
  }

  function handleClose(e: MouseEvent, id: string) {
    e.stopPropagation();
    closeTab(id);
  }

  function handleMiddleClick(e: MouseEvent, id: string) {
    if (e.button === 1) {
      e.preventDefault();
      closeTab(id);
    }
  }

  function handleNewTab() {
    openSearchTab();
  }

  // Handle deep links: URL -> open tab
  function handleWikiRoute(route: RouteState) {
    if (route.section === 'wiki' && route.projectId && route.wikiSlug) {
      openPage(route.projectId, route.wikiSlug, route.wikiSlug, undefined, projectName(route.projectId));
    } else if (route.section === 'wiki') {
      const activeTab = tabs.find(t => t.id === activeId);
      if (!activeTab || activeTab.type !== 'search') {
        const searchTab = tabs.find(t => t.type === 'search');
        if (searchTab) setActiveTab(searchTab.id);
      }
    }
  }

  onMount(() => {
    handleWikiRoute(router.getCurrentRoute());
    return router.onRouteChange(handleWikiRoute);
  });

  // Backfill projectName on tabs once project data loads (handles deep links
  // where projectsQuery hadn't resolved when the tab was created).
  $effect(() => {
    const projectList = projects.data;
    if (!projectList?.length) return;
    for (const tab of tabs) {
      if (tab.type === 'page' && tab.projectId && !tab.projectName) {
        const name = projectList.find(p => p.id === tab.projectId)?.name;
        if (name) updateTabProjectName(tab.id, name);
      }
    }
  });

  // Two-way hash sync: active tab -> URL
  $effect(() => {
    const tab = tabs.find(t => t.id === activeId);
    if (!tab) return;
    if (tab.type === 'page' && tab.projectId && tab.slug) {
      const expected = `wiki/${tab.projectId}/${tab.slug}`;
      const current = window.location.hash.replace(/^#\/?/, '');
      if (current !== expected) {
        router.navigate(`#/${expected}`);
      }
    } else if (tab.type === 'search') {
      const current = window.location.hash.replace(/^#\/?/, '');
      if (current !== 'wiki') {
        router.navigate('#/wiki');
      }
    }
  });
</script>

<div class="wiki-section">
  <Tabs.Root value={activeId} onValueChange={handleValueChange} class="wiki-tabs-root">
    <div class="tab-bar-container">
      <Tabs.List class="wiki-tab-list">
        {#each tabs as tab (tab.id)}
          <div class="tab-wrapper" onauxclick={(e: MouseEvent) => handleMiddleClick(e, tab.id)}>
            <Tabs.Trigger
              value={tab.id}
              class="wiki-tab-trigger"
            >
              <span class="tab-label" title={tab.type === 'page' ? `${tab.title} (${tab.projectName ?? tab.projectId})` : tab.title}>
                {#if tab.type === 'page' && tab.projectId}
                  <span
                    class="tab-project-dot"
                    style="background: hsl({projectHue(tab.projectId)} 50% 55%);"
                    title={tab.projectName ?? projectName(tab.projectId) ?? tab.projectId.slice(0, 8)}
                  ></span>
                {/if}
                {tab.title}
              </span>
            </Tabs.Trigger>
            {#if tabs.length > 1 && !(tabs[0] === tab && tab.type === 'search')}
              <button
                class="tab-close"
                aria-label="Close tab"
                tabindex="-1"
                onclick={(e: MouseEvent) => handleClose(e, tab.id)}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            {/if}
          </div>
        {/each}
      </Tabs.List>
      <button class="new-tab-btn" aria-label="New search tab" onclick={handleNewTab}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>
    </div>

    {#each tabs as tab (tab.id)}
      <Tabs.Content value={tab.id} class="wiki-tab-content">
        {#if tab.type === 'search'}
          <WikiSearchTab tabId={tab.id} initialProjectId={tab.projectId} />
        {:else if tab.type === 'page' && tab.projectId && tab.slug}
          <WikiPageView tabId={tab.id} projectId={tab.projectId} slug={tab.slug} />
        {/if}
      </Tabs.Content>
    {/each}
  </Tabs.Root>
</div>

<style>
  .wiki-section {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .tab-bar-container {
    display: flex;
    align-items: center;
    border-bottom: 1px solid hsl(var(--border));
    background: hsl(var(--background));
    flex-shrink: 0;
  }

  :global(.wiki-tab-list) {
    display: flex;
    overflow-x: auto;
    flex: 1;
    min-width: 0;
    scrollbar-width: thin;
  }

  :global(.wiki-tab-list::-webkit-scrollbar) {
    height: 3px;
  }

  :global(.wiki-tab-list::-webkit-scrollbar-thumb) {
    background: hsl(var(--muted));
    border-radius: 2px;
  }

  .tab-wrapper {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    position: relative;
  }

  :global(.wiki-tab-trigger) {
    display: flex;
    align-items: center;
    padding: 0.5rem 0.75rem;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    white-space: nowrap;
    max-width: 180px;
    transition: color 0.15s, border-color 0.15s;
  }

  :global(.wiki-tab-trigger:hover) {
    color: hsl(var(--foreground));
  }

  :global(.wiki-tab-trigger[data-state="active"]) {
    color: hsl(var(--foreground));
    border-bottom-color: hsl(var(--primary));
  }

  .tab-label {
    overflow: hidden;
    text-overflow: ellipsis;
    display: flex;
    align-items: center;
    gap: 0.25rem;
  }

  .tab-project-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .tab-close {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    margin-right: 0.25rem;
    margin-left: -0.375rem;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }

  .tab-close:hover {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
  }

  .tab-close svg {
    width: 12px;
    height: 12px;
  }

  .new-tab-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    margin: 0 0.375rem;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }

  .new-tab-btn:hover {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
  }

  .new-tab-btn svg {
    width: 14px;
    height: 14px;
  }

  :global(.wiki-tabs-root) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  :global(.wiki-tab-content) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  :global(.wiki-tab-content[data-state="inactive"]) {
    display: none;
  }
</style>
