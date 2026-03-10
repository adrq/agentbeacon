import type { Event as BeaconEvent, EphemeralEvent } from './types';

export interface SSEConnection {
  close: () => void;
  reconnect: () => void;
}

const MAX_CONSECUTIVE_ERRORS = 8;
const BACKOFF_THRESHOLD = 3;
const MAX_BACKOFF_MS = 30_000;

/**
 * Connect to the per-execution SSE stream.
 * Gracefully falls back to polling if endpoint returns 404 (Track A not deployed).
 * After BACKOFF_THRESHOLD consecutive errors, uses exponential backoff for reconnection.
 * After MAX_CONSECUTIVE_ERRORS, closes permanently and calls onDisconnected.
 * Handles visibility changes (laptop sleep/wake) to reconnect immediately.
 */
export function connectExecutionSSE(
  executionId: string,
  onEvent: (event: BeaconEvent) => void,
  onEphemeral?: (event: EphemeralEvent) => void,
  onConnected?: () => void,
  onDisconnected?: () => void,
  onReconnecting?: () => void,
): SSEConnection {
  let consecutiveErrors = 0;
  let closed = false;
  let connected = false;
  let disconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let backoffTimer: ReturnType<typeof setTimeout> | undefined;
  let inBackoff = false;
  let source: EventSource | null = null;

  const url = `/api/executions/${encodeURIComponent(executionId)}/events/stream`;

  function createSource() {
    if (closed) return;
    inBackoff = false;
    source = new EventSource(url);

    source.onopen = () => {
      connected = true;
      consecutiveErrors = 0;
      clearTimeout(disconnectTimer);
      console.log('[SSE] Connected to', url);
      onConnected?.();
    };

    source.onmessage = (msg) => {
      consecutiveErrors = 0;
      try {
        const event: BeaconEvent = JSON.parse(msg.data);
        onEvent(event);
      } catch {
        // Malformed JSON — skip
      }
    };

    // Listen for named "ephemeral" SSE events (streaming text deltas)
    source.addEventListener('ephemeral', ((msg: MessageEvent) => {
      consecutiveErrors = 0;
      try {
        const event: EphemeralEvent = JSON.parse(msg.data);
        onEphemeral?.(event);
      } catch { /* skip */ }
    }) as EventListener);

    source.onerror = () => {
      if (closed) return;

      if (source?.readyState === EventSource.CLOSED) {
        connected = false;
        closed = true;
        source.close();
        clearTimeout(disconnectTimer);
        console.warn('[SSE] Connection closed by server (non-200 or endpoint missing), falling back to polling');
        onDisconnected?.();
        return;
      }

      // Transient disconnect — EventSource auto-reconnects for first few errors.
      if (connected) {
        connected = false;
        clearTimeout(disconnectTimer);
        disconnectTimer = setTimeout(() => onDisconnected?.(), 2000);
      }

      consecutiveErrors++;
      console.warn(`[SSE] Transient error (${consecutiveErrors}/${MAX_CONSECUTIVE_ERRORS}), auto-reconnecting`);

      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        // Permanent fallback
        closed = true;
        source?.close();
        source = null;
        clearTimeout(disconnectTimer);
        console.warn('[SSE] Too many consecutive errors, falling back to polling permanently');
        onDisconnected?.();
      } else if (consecutiveErrors >= BACKOFF_THRESHOLD) {
        // Manual backoff: close current source and reconnect after delay
        source?.close();
        source = null;
        inBackoff = true;
        clearTimeout(disconnectTimer);
        const delay = Math.min(
          2 ** (consecutiveErrors - BACKOFF_THRESHOLD + 1) * 1000,
          MAX_BACKOFF_MS,
        );
        console.warn(`[SSE] Backoff: reconnecting in ${delay}ms`);
        onDisconnected?.();
        onReconnecting?.();
        backoffTimer = setTimeout(() => createSource(), delay);
      }
    };
  }

  // Handle visibility changes (laptop sleep/wake)
  const handleVisibility = () => {
    if (document.visibilityState === 'visible' && inBackoff && !closed) {
      clearTimeout(backoffTimer);
      console.log('[SSE] Visibility restored, reconnecting immediately');
      createSource();
    }
  };
  document.addEventListener('visibilitychange', handleVisibility);

  // Initial connection
  createSource();

  return {
    close() {
      closed = true;
      connected = false;
      inBackoff = false;
      clearTimeout(disconnectTimer);
      clearTimeout(backoffTimer);
      source?.close();
      source = null;
      document.removeEventListener('visibilitychange', handleVisibility);
    },
    reconnect() {
      closed = false;
      connected = false;
      inBackoff = false;
      consecutiveErrors = 0;
      clearTimeout(disconnectTimer);
      clearTimeout(backoffTimer);
      source?.close();
      source = null;
      document.removeEventListener('visibilitychange', handleVisibility);
      document.addEventListener('visibilitychange', handleVisibility);
      createSource();
    },
  };
}
