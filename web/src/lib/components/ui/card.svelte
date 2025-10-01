<script lang="ts">
  export let className = '';
</script>

<div class="rounded-lg border border-border bg-card text-card-foreground shadow-sm flex flex-col {className}">
  <slot />
</div>
