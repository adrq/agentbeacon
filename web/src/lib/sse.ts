import type { Event as BeaconEvent, EphemeralEvent } from './types';

export interface SSEConnection {
  close: () => void;
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
  onEphemeral?: (event: EphemeralEvent) => void,
  onConnected?: () => void,
  onDisconnected?: () => void,
): SSEConnection {
  let consecutiveErrors = 0;
  let closed = false;
  let connected = false;
  let disconnectTimer: ReturnType<typeof setTimeout> | undefined;

  const url = `/api/executions/${encodeURIComponent(executionId)}/events/stream`;
  const source = new EventSource(url);

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

    if (source.readyState === EventSource.CLOSED) {
      connected = false;
      closed = true;
      source.close();
      clearTimeout(disconnectTimer);
      console.warn('[SSE] Connection closed by server (non-200 or endpoint missing), falling back to polling');
      onDisconnected?.();
      return;
    }

    // Transient disconnect — EventSource auto-reconnects.
    // Debounce onDisconnected to avoid toggling polling on/off during
    // brief reconnect windows (onopen cancels the timer).
    if (connected) {
      connected = false;
      clearTimeout(disconnectTimer);
      disconnectTimer = setTimeout(() => onDisconnected?.(), 2000);
    }

    consecutiveErrors++;
    console.warn(`[SSE] Transient error (${consecutiveErrors}/${MAX_CONSECUTIVE_ERRORS}), auto-reconnecting`);
    if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
      closed = true;
      source.close();
      clearTimeout(disconnectTimer);
      console.warn('[SSE] Too many consecutive errors, falling back to polling permanently');
      onDisconnected?.();
    }
  };

  return {
    close() {
      if (closed) return;
      closed = true;
      connected = false;
      clearTimeout(disconnectTimer);
      source.close();
    },
  };
}
