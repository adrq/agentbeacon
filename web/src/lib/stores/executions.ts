import { writable, derived } from 'svelte/store';
import type { Execution, Agent } from '../types';
import { api } from '../api';

export const executions = writable<Execution[]>([]);
export const agents = writable<Agent[]>([]);
export const executionsLoading = writable(true);
export const executionsError = writable<string | null>(null);

export const inputRequiredCount = derived(executions, ($executions) =>
  $executions.filter(e => e.status === 'input-required').length
);

let pollTimer: ReturnType<typeof setInterval> | null = null;
let subscribers = 0;
let agentsFetched = false;

async function fetchExecutions() {
  try {
    const execs = await api.getExecutions();
    executions.set(execs);
    executionsError.set(null);
  } catch (e) {
    executionsError.set(e instanceof Error ? e.message : 'Failed to load');
  } finally {
    executionsLoading.set(false);
  }
}

async function fetchAgents() {
  try {
    const ags = await api.getAgents();
    agents.set(ags);
    agentsFetched = true;
  } catch {
    // Agents rarely change; retry on next startPolling
  }
}

export function refresh() {
  fetchExecutions();
}

export function startPolling() {
  subscribers++;
  if (subscribers === 1) {
    if (!agentsFetched) fetchAgents();
    fetchExecutions();
    pollTimer = setInterval(fetchExecutions, 5000);
  }
}

export function stopPolling() {
  subscribers--;
  if (subscribers <= 0) {
    subscribers = 0;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
}
