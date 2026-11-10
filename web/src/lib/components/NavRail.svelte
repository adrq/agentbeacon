<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { activeSection, sidebarOpen, theme, applyTheme, decisionsOverlayOpen } from '../stores/appState';
  import { decisionCount } from '../stores/questionState';
  import { router } from '../router';
  import type { NavSection } from '../types';

  interface Props {
    onToggleDecisions?: () => void;
    panelOpen?: boolean;
  }

  let { onToggleDecisions, panelOpen = false }: Props = $props();

  const sectionItems: { label: string; section: NavSection }[] = [
    { label: 'Home', section: 'home' },
    { label: 'Executions', section: 'executions' },
    { label: 'Projects', section: 'projects' },
    { label: 'Agents', section: 'agents' },
    { label: 'Wiki', section: 'wiki' },
  ];

  // Mobile "More" menu: Settings, theme and Docs, which have their own rail icons on desktop.
  let moreOpen = $state(false);
  let moreWrapper: HTMLDivElement | undefined = $state();

  $effect(() => {
    if (!moreOpen) return;
    const close = () => { moreOpen = false; };
    const onPointerDown = (e: PointerEvent) => {
      if (moreWrapper && !moreWrapper.contains(e.target as Node)) close();
    };
    const onKeydown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || !moreWrapper?.contains(document.activeElement)) return;
      close();
      moreWrapper?.querySelector<HTMLElement>('button[aria-controls="nav-more-menu"]')?.focus();
    };
    // Focus leaving the wrapper closes the menu. A press inside it can blur the
    // More button first (some browsers don't focus buttons on press), so focus
    // changes during that press are ignored.
    let pressInside = false;
    const onPressInside = () => {
      pressInside = true;
      setTimeout(() => { pressInside = false; });
    };
    const onFocusOut = (e: FocusEvent) => {
      if (pressInside) return;
      const next = e.relatedTarget as Node | null;
      if (!next || !moreWrapper?.contains(next)) close();
    };
    const wrapper = moreWrapper;
    wrapper?.addEventListener('pointerdown', onPressInside);
    wrapper?.addEventListener('focusout', onFocusOut);
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeydown);
    window.addEventListener('hashchange', close);
    return () => {
      wrapper?.removeEventListener('pointerdown', onPressInside);
      wrapper?.removeEventListener('focusout', onFocusOut);
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeydown);
      window.removeEventListener('hashchange', close);
    };
  });

  function toggleMore() {
    decisionsOverlayOpen.set(false);
    moreOpen = !moreOpen;
  }

  function openSettings() {
    moreOpen = false;
    decisionsOverlayOpen.set(false);
    router.navigate('#/settings');
  }

  function handleNavClick(section: NavSection) {
    // Any nav tap closes the decisions overlay, even when it doesn't change the route.
    decisionsOverlayOpen.set(false);
    moreOpen = false;
    if (section === 'home') {
      router.navigate('#/');
      return;
    }
    if (section === 'wiki') {
      router.navigate('#/wiki');
      return;
    }
    // Mobile has no sidebar to toggle; like an iOS tab bar, tapping a section returns to its list.
    if (window.matchMedia('(max-width: 768px)').matches) {
      router.navigate(`#/${section}`);
      return;
    }
    if ($activeSection === section && $sidebarOpen) {
      sidebarOpen.set(false);
    } else if ($activeSection === section && !$sidebarOpen) {
      sidebarOpen.set(true);
    } else {
      router.navigate(`#/${section}`);
    }
  }
</script>

<nav class="nav-rail" aria-label="Main navigation">
  <!-- Space for the brand lockup, which AppShell overlays so it never moves when the sidebar toggles. -->
  <div class="nav-rail-top desktop-only" aria-hidden="true"></div>
  {#each sectionItems as item}
    <button
      class="nav-rail-item"
      class:active={$activeSection === item.section}
      class:home-item={item.section === 'home'}
      aria-label={item.label}
      title={item.label}
      aria-current={$activeSection === item.section ? 'page' : undefined}
      onclick={() => handleNavClick(item.section)}
    >
      {#if item.section === 'home'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      {:else if item.section === 'executions'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="8" y1="6" x2="21" y2="6" />
          <line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" />
          <line x1="3" y1="6" x2="3.01" y2="6" />
          <line x1="3" y1="12" x2="3.01" y2="12" />
          <line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
      {:else if item.section === 'projects'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      {:else if item.section === 'agents'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2" />
          <circle cx="12" cy="5" r="2" />
          <path d="M12 7v4" />
          <line x1="8" y1="16" x2="8" y2="16" />
          <line x1="16" y1="16" x2="16" y2="16" />
        </svg>
      {:else if item.section === 'wiki'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
          <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
        </svg>
      {/if}
    </button>
  {/each}

  <div class="nav-rail-spacer"></div>

  <a href="/docs/" target="_blank" rel="noopener" class="nav-rail-item desktop-only" aria-label="Docs" title="Docs">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="17" x2="13" y2="17" />
    </svg>
  </a>

  <button
    class="nav-rail-item desktop-only"
    aria-label={$theme === 'dark' ? 'Activate light theme' : 'Activate dark theme'}
    aria-pressed={$theme === 'light'}
    title={$theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
    onclick={() => applyTheme($theme === 'dark' ? 'light' : 'dark')}
  >
    {#if $theme === 'dark'}
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
      </svg>
    {:else}
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="5" />
        <line x1="12" y1="1" x2="12" y2="3" />
        <line x1="12" y1="21" x2="12" y2="23" />
        <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
        <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
        <line x1="1" y1="12" x2="3" y2="12" />
        <line x1="21" y1="12" x2="23" y2="12" />
        <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
        <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
      </svg>
    {/if}
  </button>

  <button
    class="nav-rail-item decisions-toggle"
    class:panel-open={panelOpen}
    aria-label="Toggle decisions panel"
    title="Toggle decisions panel"
    onclick={onToggleDecisions}
  >
    <div class="icon-wrapper">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
        <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
      </svg>
      {#if $decisionCount > 0}
        <span class="badge">{$decisionCount > 9 ? '9+' : $decisionCount}</span>
      {/if}
    </div>
  </button>

  <button
    class="nav-rail-item desktop-only"
    class:active={$activeSection === 'settings'}
    aria-label="Settings"
    title="Settings"
    aria-current={$activeSection === 'settings' ? 'page' : undefined}
    onclick={openSettings}
  >
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6.5 1L6.2 2.6C5.8 2.8 5.4 3 5.1 3.3L3.5 2.7L2 5.3L3.4 6.4C3.4 6.6 3.3 6.8 3.3 7V7C3.3 7.2 3.4 7.4 3.4 7.6L2 8.7L3.5 11.3L5.1 10.7C5.4 11 5.8 11.2 6.2 11.4L6.5 13H9.5L9.8 11.4C10.2 11.2 10.6 11 10.9 10.7L12.5 11.3L14 8.7L12.6 7.6C12.6 7.4 12.7 7.2 12.7 7V7C12.7 6.8 12.6 6.6 12.6 6.4L14 5.3L12.5 2.7L10.9 3.3C10.6 3 10.2 2.8 9.8 2.6L9.5 1H6.5Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
      <circle cx="8" cy="7" r="2" stroke="currentColor" stroke-width="1.2"/>
    </svg>
  </button>

  <div class="more-wrapper mobile-only" bind:this={moreWrapper}>
    <button
      class="nav-rail-item"
      class:active={$activeSection === 'settings'}
      aria-label="More"
      title="More"
      aria-expanded={moreOpen}
      aria-controls="nav-more-menu"
      aria-current={$activeSection === 'settings' ? 'page' : undefined}
      onclick={toggleMore}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="5" cy="12" r="1" />
        <circle cx="12" cy="12" r="1" />
        <circle cx="19" cy="12" r="1" />
      </svg>
    </button>
    {#if moreOpen}
      <div class="more-menu" id="nav-more-menu" role="group" aria-label="More">
        <button class="more-item" onclick={openSettings}>Settings</button>
        <button
          class="more-item"
          aria-label={$theme === 'dark' ? 'Activate light theme' : 'Activate dark theme'}
          aria-pressed={$theme === 'light'}
          onclick={() => applyTheme($theme === 'dark' ? 'light' : 'dark')}
        >
          {#if $theme === 'dark'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
            Dark
          {:else}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="5" />
              <line x1="12" y1="1" x2="12" y2="3" />
              <line x1="12" y1="21" x2="12" y2="23" />
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
              <line x1="1" y1="12" x2="3" y2="12" />
              <line x1="21" y1="12" x2="23" y2="12" />
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
            </svg>
            Light
          {/if}
        </button>
        <a class="more-item" href="/docs/" target="_blank" rel="noopener" onclick={() => { moreOpen = false; }}>
          Docs
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17L17 7" /><path d="M7 7h10v10" /></svg>
        </a>
      </div>
    {/if}
  </div>
</nav>

<style>
  .nav-rail {
    width: var(--rail-width);
    flex: 0 0 var(--rail-width);
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0 0 0.5rem;
    gap: 0.25rem;
    border-right: 1px solid hsl(var(--border));
    background: hsl(var(--background));
  }

  .nav-rail-top {
    width: 100%;
    height: var(--top-row-height);
    flex-shrink: 0;
    margin-bottom: 0.25rem;
  }

  .nav-rail-item {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius);
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    position: relative;
    transition: color 0.15s, background 0.15s;
  }

  .nav-rail-item:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .nav-rail-item.active {
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.1);
  }

  .nav-rail-item.active::before {
    content: '';
    position: absolute;
    left: -6px;
    top: 50%;
    transform: translateY(-50%);
    width: 3px;
    height: 20px;
    border-radius: 0 2px 2px 0;
    background: hsl(var(--primary));
  }

  .nav-rail-item.home-item.active::before {
    display: none;
  }

  .nav-rail-item svg {
    width: 18px;
    height: 18px;
  }

  .nav-rail-spacer {
    flex: 1;
  }

  .mobile-only {
    display: none;
  }

  .more-wrapper {
    position: relative;
  }

  .more-menu {
    position: absolute;
    bottom: calc(100% + 0.5rem);
    right: 0;
    min-width: 10rem;
    padding: 0.25rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    box-shadow: 0 4px 12px hsl(var(--shadow-hsl) / 0.2);
    display: flex;
    flex-direction: column;
    gap: 0.0625rem;
  }

  .more-item {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.5rem 0.75rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-weight: 500;
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }

  .more-item:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .more-item svg {
    width: 14px;
    height: 14px;
  }

  .decisions-toggle.panel-open {
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.1);
    box-shadow: inset 0 0 0 1.5px hsl(var(--primary) / 0.3);
  }

  .icon-wrapper {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .badge {
    position: absolute;
    top: -6px;
    right: -8px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: hsl(var(--status-danger));
    color: hsl(0 0% 100%);
    font-size: 0.625rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
  }

  @media (max-width: 768px) {
    .nav-rail {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      width: 100%;
      height: auto;
      flex-direction: row;
      justify-content: space-around;
      border-right: none;
      border-top: 1px solid hsl(var(--border));
      padding: 0 0 env(safe-area-inset-bottom, 0px);
      min-height: 48px;
      z-index: 100;
    }

    .nav-rail-spacer,
    .desktop-only {
      display: none;
    }

    .mobile-only {
      display: flex;
    }

    .nav-rail-item.active::before {
      left: 50%;
      top: 0;
      transform: translateX(-50%);
      width: 20px;
      height: 3px;
      border-radius: 0 0 2px 2px;
    }
  }
</style>
