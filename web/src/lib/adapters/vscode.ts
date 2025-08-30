import type { EnvironmentAdapter } from './types.js';

// VS Code extension API types (simplified)
declare global {
  interface Window {
    acquireVsCodeApi?: () => {
      postMessage: (message: any) => void;
      setState: (state: any) => void;
      getState: () => any;
    };
  }
}

export class VSCodeAdapter implements EnvironmentAdapter {
  readonly name = 'vscode';
  readonly isVSCode = true;
  readonly isStandalone = false;

  private vscode: ReturnType<NonNullable<typeof window.acquireVsCodeApi>> | null = null;

  constructor() {
    if (window.acquireVsCodeApi) {
      this.vscode = window.acquireVsCodeApi();
    }
  }

  showNotification(message: string, type: 'info' | 'warning' | 'error' = 'info'): void {
    if (this.vscode) {
      this.vscode.postMessage({
        command: 'notification',
        type,
        message
      });
    } else {
      console[type === 'error' ? 'error' : type === 'warning' ? 'warn' : 'log'](message);
    }
  }

  openExternalLink(url: string): void {
    if (this.vscode) {
      this.vscode.postMessage({
        command: 'openExternal',
        url
      });
    } else {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  async copyToClipboard(text: string): Promise<boolean> {
    if (this.vscode) {
      return new Promise((resolve) => {
        const messageId = Date.now().toString();

        // Listen for response
        const handleMessage = (event: MessageEvent) => {
          if (event.data.id === messageId) {
            window.removeEventListener('message', handleMessage);
            resolve(event.data.success === true);
          }
        };

        window.addEventListener('message', handleMessage);

        this.vscode!.postMessage({
          command: 'clipboard',
          text,
          id: messageId
        });

        // Timeout after 5 seconds
        setTimeout(() => {
          window.removeEventListener('message', handleMessage);
          resolve(false);
        }, 5000);
      });
    } else {
      // Fallback to web API
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(text);
          return true;
        }
        return false;
      } catch {
        return false;
      }
    }
  }

  async saveFile(content: string, filename: string): Promise<boolean> {
    if (this.vscode) {
      return new Promise((resolve) => {
        const messageId = Date.now().toString();

        // Listen for response
        const handleMessage = (event: MessageEvent) => {
          if (event.data.id === messageId) {
            window.removeEventListener('message', handleMessage);
            resolve(event.data.success === true);
          }
        };

        window.addEventListener('message', handleMessage);

        this.vscode!.postMessage({
          command: 'saveFile',
          content,
          filename,
          id: messageId
        });

        // Timeout after 10 seconds
        setTimeout(() => {
          window.removeEventListener('message', handleMessage);
          resolve(false);
        }, 10000);
      });
    }

    return false; // Not supported in VS Code fallback
  }

  async loadFile(): Promise<string | null> {
    if (this.vscode) {
      return new Promise((resolve) => {
        const messageId = Date.now().toString();

        // Listen for response
        const handleMessage = (event: MessageEvent) => {
          if (event.data.id === messageId) {
            window.removeEventListener('message', handleMessage);
            resolve(event.data.content || null);
          }
        };

        window.addEventListener('message', handleMessage);

        this.vscode!.postMessage({
          command: 'loadFile',
          id: messageId
        });

        // Timeout after 10 seconds
        setTimeout(() => {
          window.removeEventListener('message', handleMessage);
          resolve(null);
        }, 10000);
      });
    }

    return null; // Not supported in VS Code fallback
  }
}
