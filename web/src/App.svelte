<script lang="ts">
  import './lib/router';
  import { QueryClientProvider, QueryClient } from '@tanstack/svelte-query';
  import AppShell from './lib/components/AppShell.svelte';
  import Toaster from './lib/components/Toaster.svelte';
  import { clearWindows, dropCache, fullKey, windowKey } from './lib/historyWindow';
  import { clearSharedReads, discardSharedRead } from './lib/historyReads';
  import { onDestroy } from 'svelte';

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 2000,
        retry: 1,
      },
    },
  });

  // A history's paging state is dropped when its rows are. Each cache is
  // tracked separately: they are evicted independently.
  const unsubscribe = queryClient.getQueryCache().subscribe(event => {
    if (event.type !== 'removed') return;
    const [name, id] = event.query.queryKey as [string, string];
    if (typeof id !== 'string') return;
    if (name === 'session-events') {
      dropCache(windowKey(id));
      discardSharedRead(id);
    }
    if (name === 'session-events-full') dropCache(fullKey(id));
  });

  // Dropped with this client: the subscription, the paging metadata, and any
  // shared read.
  onDestroy(() => {
    unsubscribe();
    clearWindows();
    clearSharedReads();
  });
</script>

<div class="app-shell">
  <QueryClientProvider client={queryClient}>
    <AppShell />
    <Toaster />
  </QueryClientProvider>
</div>

<style>
  .app-shell {
    width: 100%;
    height: 100vh;
    height: 100dvh;
    display: flex;
    flex-direction: column;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    overflow: hidden;
  }

  @media (max-width: 768px) {
    .app-shell {
      position: fixed;
      inset: 0;
      height: auto;
    }
  }
</style>
