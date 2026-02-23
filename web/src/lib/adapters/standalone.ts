import type { EnvironmentAdapter } from './types.js';
import { toasts } from '../stores/toasts';
import { notificationsEnabled } from '../stores/appState';
import { setOnNewDecisionCallback, type DecisionItem } from '../stores/questionState';
import { get } from 'svelte/store';

const notifiedSessions = new Set<string>();
const MAX_NOTIFIED_CACHE = 100;

function setupNotificationCallback() {
  setOnNewDecisionCallback((item: DecisionItem) => {
    if (!get(notificationsEnabled)) return;
    if (!document.hidden) return;
    const notifKey = `${item.sessionId}:${item.batchId}`;
    if (notifiedSessions.has(notifKey)) return;

    if ('Notification' in window && Notification.permission === 'granted') {
      const question = item.questions[0]?.questionText ?? 'New question';
      new Notification('AgentBeacon: Question pending', {
        body: `${item.executionTitle ?? 'Execution'}: ${question}`,
      });
      notifiedSessions.add(notifKey);
      // Prevent unbounded growth — evict oldest entries
      if (notifiedSessions.size > MAX_NOTIFIED_CACHE) {
        const first = notifiedSessions.values().next().value;
        if (first) notifiedSessions.delete(first);
      }
    }
  });
}

export async function requestNotificationPermission(): Promise<boolean> {
  try {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') {
      notificationsEnabled.set(true);
      return true;
    }
    if (Notification.permission === 'denied') return false;
    const result = await Notification.requestPermission();
    if (result === 'granted') {
      notificationsEnabled.set(true);
      return true;
    }
    return false;
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

// Register notification callback at module load time so it runs when
// DecisionCard imports requestNotificationPermission (the only import path).
setupNotificationCallback();
