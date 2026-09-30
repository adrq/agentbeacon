// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { Event as BeaconEvent, EphemeralEvent } from './types';
import type { ProblemDetails } from './api';
import { streamUrl } from './sseBatch';
import { isUnsupportedSchema } from './eventSchema';

export interface SSEConnection {
  close: () => void;
  reconnect: () => void;
}

// Payload of the `position` event.
export interface StreamPosition {
  position: string | null;
  history_before: string | null;
}

const MAX_CONSECUTIVE_ERRORS = 8;
const BACKOFF_THRESHOLD = 3;
const MAX_BACKOFF_MS = 30_000;

/** True for an execution-level terminal state change. */
function isExecutionTerminal(event: BeaconEvent): boolean {
  // An unreadable payload is never terminal.
  if (isUnsupportedSchema(event)) return false;
  if (event.session_id !== null || event.event_type !== 'state_change') return false;
  const payload = event.payload as Record<string, unknown>;
  const outcome = (payload?.outcome ?? payload?.to) as string | undefined;
  return outcome === 'completed' || outcome === 'failed' || outcome === 'canceled';
}

/**
 * Connect to the per-execution SSE stream.
 *
 * `onPermanentFallback` runs at every point this connection stops for good,
 * before `onDisconnected`.
 * Gracefully falls back to polling if endpoint returns 404.
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
  onPosition?: (position: StreamPosition) => void,
  onProtocolError?: (problem: ProblemDetails) => void,
  onPermanentFallback?: () => void,
): SSEConnection {
  let consecutiveErrors = 0;
  let closed = false;
  // Set when this connection must not be reopened.
  let fatal = false;
  let connected = false;
  let disconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let backoffTimer: ReturnType<typeof setTimeout> | undefined;
  let inBackoff = false;
  let source: EventSource | null = null;

  const base = `/api/v1/executions/${encodeURIComponent(executionId)}/events/stream`;

  function createSource() {
    if (closed) return;
    inBackoff = false;
    const url = streamUrl(base);
    source = new EventSource(url);

    source.onopen = () => {
      if (fatal) return;
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
        if (isExecutionTerminal(event)) {
          closed = true;
          connected = false;
          clearTimeout(disconnectTimer);
          source?.close();
          source = null;
          onDisconnected?.();
        }
      } catch {
        // Malformed JSON — skip
      }
    };

    source.addEventListener('position', ((msg: MessageEvent) => {
      consecutiveErrors = 0;
      try {
        onPosition?.(JSON.parse(msg.data) as StreamPosition);
      } catch { /* skip */ }
    }) as EventListener);

    source.addEventListener('protocol_error', ((msg: MessageEvent) => {
      try {
        onProtocolError?.(JSON.parse(msg.data) as ProblemDetails);
      } catch { /* skip */ }
      // Close and report disconnected, which starts the polling fallback.
      fatal = true;
      closed = true;
      connected = false;
      clearTimeout(disconnectTimer);
      source?.close();
      source = null;
      onPermanentFallback?.();
      onDisconnected?.();
    }) as EventListener);

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
        onPermanentFallback?.();
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
        onPermanentFallback?.();
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
      fatal = false;
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
