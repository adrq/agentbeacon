<script lang="ts">
  import './lib/router';
  import { QueryClientProvider, QueryClient } from '@tanstack/svelte-query';
  import AppShell from './lib/components/AppShell.svelte';

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 2000,
        retry: 1,
      },
    },
  });
</script>

<div class="app-shell">
  <QueryClientProvider client={queryClient}>
    <AppShell />
  </QueryClientProvider>
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
