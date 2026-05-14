import type { EnvironmentAdapter } from './types.js';
import type { Execution } from '../types';
import { toasts } from '../stores/toasts';
import { escalationNotificationsEnabled, turnCompleteNotificationsEnabled } from '../stores/appState';
import { setOnNewDecisionCallback, type DecisionItem } from '../stores/questionState';
import { get } from 'svelte/store';

const NOTIFIED_KEY = 'agentbeacon-notified-sessions';
const COOLDOWN_KEY = 'agentbeacon-notification-cooldowns';
const MAX_NOTIFIED_CACHE = 100;
const COOLDOWN_MS = 10_000;

// Persist across refresh and tabs (localStorage is shared cross-tab, survives refresh)
const notifiedSessions: Set<string> = (() => {
  try {
    const raw = localStorage.getItem(NOTIFIED_KEY);
    return raw ? new Set<string>(JSON.parse(raw)) : new Set<string>();
  } catch { return new Set<string>(); }
})();

function persistNotified() {
  try {
    localStorage.setItem(NOTIFIED_KEY, JSON.stringify([...notifiedSessions]));
  } catch { /* quota exceeded — silent */ }
}

// Per-execution cooldown: suppress duplicate notifications when escalation
// and turn-complete fire in quick succession for the same execution.
function isOnCooldown(executionId: string): boolean {
  try {
    const raw = localStorage.getItem(COOLDOWN_KEY);
    if (!raw) return false;
    const cooldowns: Record<string, number> = JSON.parse(raw);
    const ts = cooldowns[executionId];
    return typeof ts === 'number' && Date.now() - ts < COOLDOWN_MS;
  } catch { return false; }
}

function setCooldown(executionId: string) {
  try {
    const raw = localStorage.getItem(COOLDOWN_KEY);
    const cooldowns: Record<string, number> = raw ? JSON.parse(raw) : {};
    cooldowns[executionId] = Date.now();
    // Prune stale entries
    const now = Date.now();
    for (const key of Object.keys(cooldowns)) {
      if (now - cooldowns[key] > COOLDOWN_MS * 6) delete cooldowns[key];
    }
    localStorage.setItem(COOLDOWN_KEY, JSON.stringify(cooldowns));
  } catch { /* ignore */ }
}

function fireEscalationNotification(item: DecisionItem) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;

  // Re-read from localStorage so writes from other tabs are visible
  try {
    const raw = localStorage.getItem(NOTIFIED_KEY);
    if (raw) for (const id of JSON.parse(raw) as string[]) notifiedSessions.add(id);
  } catch { /* ignore parse errors */ }

  const notifKey = `${item.sessionId}:${item.batchId}`;
  if (notifiedSessions.has(notifKey)) return;

  // Escalation notifications are high-priority and always fire (no cooldown check).
  // They DO set the cooldown so subsequent turn-complete notifications get suppressed.
  const question = item.questions[0]?.questionText ?? 'New question';
  const n = new Notification('AgentBeacon: Question pending', {
    body: `${item.executionTitle ?? 'Execution'}: ${question}`,
  });
  n.onclick = () => {
    window.focus();
    window.location.hash = `/execution/${item.executionId}`;
    n.close();
  };
  notifiedSessions.add(notifKey);
  if (notifiedSessions.size > MAX_NOTIFIED_CACHE) {
    const first = notifiedSessions.values().next().value;
    if (first) notifiedSessions.delete(first);
  }
  persistNotified();
  setCooldown(item.executionId);
}

export function fireTurnCompleteNotification(execution: Execution) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  if (!get(turnCompleteNotificationsEnabled)) return;
  if (isOnCooldown(execution.id)) return;

  const n = new Notification('AgentBeacon: Turn complete', {
    body: execution.title ?? 'Execution',
  });
  n.onclick = () => {
    window.focus();
    window.location.hash = `/execution/${execution.id}`;
    n.close();
  };
  setCooldown(execution.id);
}

function setupNotificationCallback() {
  setOnNewDecisionCallback((item: DecisionItem) => {
    if (!get(escalationNotificationsEnabled)) return;
    fireEscalationNotification(item);
  });
}

export async function requestNotificationPermission(): Promise<boolean> {
  try {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    const result = await Notification.requestPermission();
    return result === 'granted';
  } catch {
    return false;
  }
}

export class StandaloneAdapter implements EnvironmentAdapter {
  readonly name = 'standalone';
  readonly isVSCode = false;
  readonly isStandalone = true;

  showNotification(message: string, type: 'info' | 'warning' | 'error' = 'info'): void {
    if (type === 'error' || type === 'warning') {
      toasts.error(message);
    } else {
      toasts.info(message);
    }
  }

  openExternalLink(url: string): void {
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  async copyToClipboard(text: string): Promise<boolean> {
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        return true;
      } else {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.opacity = '0';
        document.body.appendChild(textArea);
        textArea.select();
        const success = document.execCommand('copy');
        document.body.removeChild(textArea);
        return success;
      }
    } catch (error) {
      console.error('Failed to copy to clipboard:', error);
      return false;
    }
  }

  async saveFile(content: string, filename: string): Promise<boolean> {
    try {
      const blob = new Blob([content], { type: 'text/yaml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return true;
    } catch (error) {
      console.error('Failed to save file:', error);
      return false;
    }
  }

  async loadFile(): Promise<string | null> {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.yaml,.yml';

      input.onchange = (event) => {
        const file = (event.target as HTMLInputElement).files?.[0];
        if (file) {
          const reader = new FileReader();
          reader.onload = (e) => resolve(e.target?.result as string);
          reader.onerror = () => resolve(null);
          reader.readAsText(file);
        } else {
          resolve(null);
        }
      };

      input.click();
    });
  }
}

// Register notification callback at module load time.
setupNotificationCallback();
