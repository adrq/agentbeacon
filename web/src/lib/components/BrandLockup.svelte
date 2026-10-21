<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { theme } from '../stores/appState';
  import logoDarkUrl from '../../assets/logo-mark-dark.svg';
  import logoLightUrl from '../../assets/logo-mark-light.svg';

  let logoUrl = $derived($theme === 'light' ? logoLightUrl : logoDarkUrl);
  let wordmarkEl: HTMLSpanElement | undefined = $state();

  // The sidebar brand row and execution header reserve space up to the wordmark's right edge.
  $effect(() => {
    const el = wordmarkEl;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const right = el.getBoundingClientRect().right;
      if (right > 0) document.documentElement.style.setProperty('--brand-lockup-width', `${Math.ceil(right)}px`);
    });
    observer.observe(el);
    // Rail width changes at breakpoints shift the wordmark without resizing it.
    if (el.parentElement) observer.observe(el.parentElement);
    return () => observer.disconnect();
  });
</script>

<a href="#/" class="brand-lockup" aria-label="AgentBeacon home">
  <img src={logoUrl} alt="" class="logo" width="24" height="24" />
  <span class="wordmark" bind:this={wordmarkEl}>AgentBeacon</span>
</a>

<style>
  /* One element pinned to the corner, so it stays still while the sidebar animates beneath it. */
  .brand-lockup {
    position: absolute;
    top: 0;
    left: 0;
    z-index: 20;
    height: var(--top-row-height);
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding-left: calc((var(--rail-width) - 1.5rem) / 2);
    border-bottom: 1px solid hsl(var(--border));
    background: hsl(var(--background));
    text-decoration: none;
    color: hsl(var(--foreground));
  }

  .logo {
    width: 1.5rem;
    height: 1.5rem;
    flex-shrink: 0;
  }

  .wordmark {
    font-size: 0.875rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    white-space: nowrap;
    user-select: none;
  }

  @media (max-width: 768px) {
    .brand-lockup {
      display: none;
    }
  }
</style>
