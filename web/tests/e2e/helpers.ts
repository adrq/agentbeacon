/**
 * Shared E2E test helpers — API clients, wait utilities, agent lookups.
 */

export const API_URL = process.env.API_URL ?? 'http://localhost:9456';

export const TERMINAL = new Set(['completed', 'failed', 'canceled']);

export async function apiPost(path: string, body: unknown) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiGet(path: string) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiDelete(path: string) {
  const res = await fetch(`${API_URL}${path}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`API ${path} failed: ${res.status}`);
}

/**
 * Cancel all non-terminal executions and poll until every execution is terminal.
 * Deterministic replacement for sleep-based "settle" waits.
 */
export async function waitForWorkerIdle(timeoutMs = 15000) {
  const execs: { id: string; status: string }[] = await apiGet('/api/executions');
  for (const exec of execs) {
    if (!TERMINAL.has(exec.status)) {
      try { await apiPost(`/api/executions/${exec.id}/cancel`, {}); } catch { /* best effort */ }
    }
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const current: { id: string; status: string }[] = await apiGet('/api/executions');
    if (current.every(e => TERMINAL.has(e.status))) return;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Worker did not become idle within ${timeoutMs}ms`);
}

/** Wait for execution to be picked up by worker (working/input-required/completed). */
export async function waitForWorkerPickup(execId: string, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await apiGet(`/api/executions/${execId}`);
    const status = data.execution?.status ?? data.status;
    if (status === 'working' || status === 'input-required' || status === 'completed') return;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Worker did not pick up execution ${execId} within ${timeoutMs}ms`);
}

/** Wait for execution to reach a terminal state. */
export async function waitForTerminal(execId: string, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await apiGet(`/api/executions/${execId}`);
    const status = data.execution?.status ?? data.status;
    if (TERMINAL.has(status)) return status;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Execution ${execId} did not reach terminal state within ${timeoutMs}ms`);
}

/** Wait for execution to finish its turn (input-required or terminal). */
export async function waitForTurnEnd(execId: string, timeoutMs = 20000) {
  const ready = new Set(['input-required', 'completed', 'failed', 'canceled']);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await apiGet(`/api/executions/${execId}`);
    const status = data.execution?.status ?? data.status;
    if (ready.has(status)) return status;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Execution ${execId} did not finish turn within ${timeoutMs}ms`);
}

/** Wait for execution to reach working state. */
export async function waitForWorking(execId: string, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await apiGet(`/api/executions/${execId}`);
    const status = data.execution?.status ?? data.status;
    if (status === 'working') return status;
    if (TERMINAL.has(status))
      throw new Error(`Execution ${execId} reached terminal ${status} before working`);
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Execution ${execId} did not reach working state within ${timeoutMs}ms`);
}

/** Ensure the 'Mock Agent (Direct)' exists, creating if needed. */
export async function ensureDirectAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  const existing = agents.find(a => a.name === 'Mock Agent (Direct)');
  if (existing) return { id: existing.id, name: existing.name };

  const result = await apiPost('/api/agents', {
    name: 'Mock Agent (Direct)',
    agent_type: 'acp',
    description: 'Mock ACP agent without scenario for special command tests',
    config: {
      command: 'uv',
      args: ['run', 'python', '-m', 'agentbeacon.mock_agent', '--mode', 'acp'],
      timeout: 60,
    },
  });
  return { id: result.id, name: result.name };
}

/** Ensure the 'Demo Agent' exists (must be pre-seeded via scripts/seed_agents.py). */
export async function ensureDemoAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string; agent_type?: string }[] = await apiGet('/api/agents');
  const demo = agents.find(a => a.name === 'Demo Agent');
  if (!demo) throw new Error('Demo Agent not found — run scripts/seed_agents.py first');
  if (demo.agent_type && demo.agent_type !== 'acp') {
    throw new Error(`Demo Agent has unexpected type: ${demo.agent_type}`);
  }
  return { id: demo.id, name: demo.name };
}

/** Ensure the 'Showcase Agent' exists (must be pre-seeded via scripts/seed_agents.py). */
export async function ensureShowcaseAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string; agent_type?: string }[] = await apiGet('/api/agents');
  const showcase = agents.find(a => a.name === 'Showcase Agent');
  if (!showcase) throw new Error('Showcase Agent not found — run scripts/seed_agents.py first');
  return { id: showcase.id, name: showcase.name };
}

/** Ensure the 'Claude Code' agent exists (must be pre-seeded via scripts/seed_agents.py). */
export async function ensureClaudeAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string; agent_type?: string }[] = await apiGet('/api/agents');
  const claude = agents.find(a => a.name === 'Claude Code');
  if (!claude) throw new Error('Claude Code agent not found — run scripts/seed_agents.py first');
  return { id: claude.id, name: claude.name };
}

/** Ensure the 'GitHub Copilot' agent exists (must be pre-seeded via scripts/seed_agents.py). */
export async function ensureCopilotAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string; agent_type?: string }[] = await apiGet('/api/agents');
  const copilot = agents.find(a => a.name === 'GitHub Copilot');
  if (!copilot) throw new Error('GitHub Copilot agent not found — run scripts/seed_agents.py first');
  return { id: copilot.id, name: copilot.name };
}

/** Create an execution and return its IDs. */
export async function createExecution(agentId: string, prompt: string, title: string) {
  const exec = await apiPost('/api/executions', {
    agent_id: agentId,
    prompt,
    title,
    cwd: '/tmp',
  });
  return { execId: exec.execution.id, sessionId: exec.session_id };
}
