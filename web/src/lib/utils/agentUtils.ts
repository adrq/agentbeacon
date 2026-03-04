export const typeLabels: Record<string, string> = {
  claude_sdk: 'Claude',
  codex_sdk: 'Codex',
  copilot_sdk: 'Copilot',
  opencode_sdk: 'OpenCode',
  acp: 'ACP',
  a2a: 'A2A',
};

export interface AgentTemplate {
  name: string;
  platform: string;
  description: string;
  config: Record<string, unknown>;
}

export const agentTemplates: AgentTemplate[] = [
  {
    name: 'Claude Code',
    platform: 'claude_sdk',
    description: 'Claude Code via Agent SDK',
    config: { command: 'claude', args: [], timeout: 300, env: {}, state_dir: '~/.claude' },
  },
  {
    name: 'Copilot',
    platform: 'copilot_sdk',
    description: 'GitHub Copilot Coding Agent',
    config: { command: 'copilot-agent', args: [], timeout: 300, env: {} },
  },
  {
    name: 'Codex',
    platform: 'codex_sdk',
    description: 'OpenAI Codex CLI Agent',
    config: { command: 'codex', args: [], timeout: 300, env: {} },
  },
  {
    name: 'OpenCode',
    platform: 'opencode_sdk',
    description: 'OpenCode Agent',
    config: { command: 'opencode', args: [], timeout: 300, env: {} },
  },
];
