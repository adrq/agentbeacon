<script lang="ts">
  import './lib/router';
  import AppShell from './lib/components/AppShell.svelte';
</script>

<div class="app-shell">
  <AppShell />
</div>

<style>
  .app-shell {
    width: 100%;
    height: 100vh;
    display: flex;
    flex-direction: column;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    overflow: hidden;
  }
</style>
