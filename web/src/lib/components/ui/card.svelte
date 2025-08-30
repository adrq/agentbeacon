<script lang="ts">
  import { cn } from '../../utils/cn';
  export let className = '';
</script>
<div class={cn('rounded-lg border border-border bg-card text-card-foreground shadow-sm flex flex-col', className)}>
  <slot />
</div>
