<script lang="ts">
  export let className = '';
</script>

<div class="p-4 border-b flex items-center justify-between {className}">
  <slot />
</div>
