import type { Event as BeaconEvent } from './types';

export interface SSEConnection {
  close: () => void;
  readonly connected: boolean;
}

const MAX_CONSECUTIVE_ERRORS = 3;

/**
 * Connect to the per-execution SSE stream.
 * Gracefully falls back to polling if endpoint returns 404 (Track A not deployed).
 * Limits reconnection attempts: after 3 consecutive errors without a successful
 * message, closes permanently and calls onDisconnected.
 */
export function connectExecutionSSE(
  executionId: string,
  onEvent: (event: BeaconEvent) => void,
  onConnected?: () => void,
  onDisconnected?: () => void,
): SSEConnection {
  let consecutiveErrors = 0;
  let closed = false;
  let connected = false;

  const url = `/api/executions/${encodeURIComponent(executionId)}/events/stream`;
  const source = new EventSource(url);

  source.onopen = () => {
    connected = true;
    consecutiveErrors = 0;
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

  source.onerror = () => {
    if (closed) return;

    if (source.readyState === EventSource.CLOSED) {
      // Server returned non-200 (e.g., 404 — endpoint not deployed yet)
      connected = false;
      closed = true;
      source.close();
      console.warn('SSE connection failed, falling back to polling');
      onDisconnected?.();
      return;
    }

    // Transient disconnect — EventSource auto-reconnects.
    // Mark disconnected immediately so polling re-enables during the
    // reconnect window (onopen will restore connected state).
    if (connected) {
      connected = false;
      onDisconnected?.();
    }

    consecutiveErrors++;
    if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
      closed = true;
      source.close();
      console.warn('SSE: too many consecutive errors, falling back to polling');
    }
  };

  return {
    close() {
      if (closed) return;
      closed = true;
      connected = false;
      source.close();
    },
    get connected() {
      return connected;
    },
  };
}
