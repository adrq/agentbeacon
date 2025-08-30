// Environment adapter interface
export interface EnvironmentAdapter {
  readonly name: string;
  readonly isVSCode: boolean;
  readonly isStandalone: boolean;

  // Environment-specific methods
  showNotification(message: string, type?: 'info' | 'warning' | 'error'): void;
  openExternalLink(url: string): void;
  copyToClipboard(text: string): Promise<boolean>;

  // File operations (if supported)
  saveFile?(content: string, filename: string): Promise<boolean>;
  loadFile?(): Promise<string | null>;
}
