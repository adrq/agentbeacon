<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let items: { label: string; value: string }[] = [];
  export let value: string = '';
  export let disabled = false;
  export let placeholder = 'Select...';
  export let className = '';

  const dispatch = createEventDispatcher<{ change: string }>();

  function handleChange(e: Event) {
    const target = e.target as HTMLSelectElement;
    value = target.value;
    dispatch('change', value);
  }
</script>

<select
  class="h-9 rounded-md border bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 {className}"
  bind:value
  {disabled}
  on:change={handleChange}
  {...$$restProps}
>
  <option value="">{placeholder}</option>
  {#each items as item}
    <option value={item.value}>{item.label}</option>
  {/each}
</select>
