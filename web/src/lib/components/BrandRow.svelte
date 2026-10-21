<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import Button from './ui/button.svelte';
  import { router } from '../router';

  interface Props {
    showNew?: boolean;
  }

  let { showNew = false }: Props = $props();
</script>

<!-- The brand lockup in AppShell sits over the left of this row. -->
<div class="brand-row">
  {#if showNew}
    <Button variant="default" size="sm" onclick={() => router.navigate('/executions/new')}>
      + New
    </Button>
  {/if}
</div>

<style>
  .brand-row {
    height: var(--top-row-height);
    box-sizing: border-box;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 0 1rem;
    border-bottom: 1px solid hsl(var(--border));
  }

  @media (max-width: 768px) {
    .brand-row {
      display: none;
    }
  }
</style>
