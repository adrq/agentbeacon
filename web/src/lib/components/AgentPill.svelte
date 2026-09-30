<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  interface Props {
    name: string;
  }

  let { name }: Props = $props();
</script>

<span class="agent-pill">{name}</span>

<style>
  .agent-pill {
    display: inline-block;
    padding: 0.0625rem 0.3125rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.1);
    color: hsl(var(--primary));
    font-size: 0.625rem;
    font-weight: 500;
    flex-shrink: 0;
  }
</style>
