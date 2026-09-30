// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

// Merges two sessions' histories into one thread.
import type { Event, MessagePayload, MessagePart } from './types';
import { isMessagePayload } from './types';
import { isUnsupportedSchema } from './eventSchema';

// The event kinds this client knows. Anything else was added after it shipped.
const KNOWN_EVENT_TYPES = new Set(['message', 'state_change', 'platform', 'escalate']);

// A part paired with whether the server emptied its value.
export interface ThreadPart {
  part: MessagePart;
  omitted: boolean;
}

export interface ThreadEntry {
  eventId: string;
  executionId: string;
  truncated?: boolean;
  // True when this client cannot read the event: an unknown kind, or a payload
  // schema it cannot parse.
  unsupported?: boolean;
  senderSessionId: string;
  senderSlug: string;
  parts: ThreadPart[];
  time: string;
}

// The two sides of a thread and the labels they render under.
export interface ThreadSides {
  sessionA: string;
  slugA: string;
  sessionB: string;
  slugB: string;
}

/** True when the event carries a sender part naming this session. */
export function hasSenderPart(event: Event, senderSessionId: string): boolean {
  if (isUnsupportedSchema(event)) return false;
  if (event.event_type !== 'message' || !isMessagePayload(event.payload)) return false;
  return event.payload.parts.some((p: MessagePart) =>
    'data' in p && typeof p.data === 'object' && p.data !== null &&
    (p.data as Record<string, unknown>).type === 'sender' &&
    (p.data as Record<string, unknown>).session_id === senderSessionId
  );
}

/** Renderable parts, each carrying whether the server emptied its value. */
export function extractParts(payload: MessagePayload, omittedPaths: string[]): ThreadPart[] {
  return payload.parts
    .map((part: MessagePart, index: number) => ({
      part,
      omitted: omittedPaths.includes(`parts[${index}].raw`),
    }))
    .filter(({ part }) => {
      if ('data' in part && typeof part.data === 'object' && part.data !== null) {
        const d = part.data as Record<string, unknown>;
        if (d.type === 'sender') return false;
      }
      return true;
    });
}

function defaultFormatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Merge two server-ordered histories into one thread, oldest first.
 *
 * Each list keeps the order the server returned it in. `created_at` chooses
 * only which list to take from next; ids are never ordered.
 */
export function buildThread(
  eventsA: Event[],
  eventsB: Event[],
  sides: ThreadSides,
  formatTime: (iso: string) => string = defaultFormatTime,
): ThreadEntry[] {
  const timed = (entry: ThreadEntry, at: number) => ({ entry, at });

  const slugFor = (sessionId: string | null) =>
    sessionId === sides.sessionA ? sides.slugA : sides.slugB;

  const entriesFor = (events: Event[], senderSessionId: string, senderSlug: string) => {
    const out: { entry: ThreadEntry; at: number }[] = [];
    for (const ev of events) {
      const at = new Date(ev.created_at).getTime();
      // An event this client cannot read is attributed from its own session and
      // carries no parts. A kind it does not know is such an event: only the
      // kinds above are deliberately absent from a thread.
      if (isUnsupportedSchema(ev) || !KNOWN_EVENT_TYPES.has(ev.event_type)) {
        out.push(
          timed(
            {
              eventId: ev.id,
              executionId: ev.execution_id,
              unsupported: true,
              senderSessionId: ev.session_id ?? senderSessionId,
              senderSlug: slugFor(ev.session_id),
              parts: [],
              time: formatTime(ev.created_at),
            },
            at,
          ),
        );
        continue;
      }
      if (!hasSenderPart(ev, senderSessionId)) continue;
      out.push(
        timed(
          {
            eventId: ev.id,
            executionId: ev.execution_id,
            senderSessionId,
            senderSlug,
            parts: extractParts(ev.payload as MessagePayload, ev.omitted_paths ?? []),
            time: formatTime(ev.created_at),
            truncated: ev.truncated,
          },
          at,
        ),
      );
    }
    return out;
  };

  const listA = entriesFor(eventsA, sides.sessionB, sides.slugB);
  const listB = entriesFor(eventsB, sides.sessionA, sides.slugA);

  // Each list advances only from its own head; ties go to A.
  const merged: ThreadEntry[] = [];
  const seen = new Set<string>();
  let i = 0;
  let j = 0;
  while (i < listA.length || j < listB.length) {
    let next: { entry: ThreadEntry; at: number };
    if (i >= listA.length) {
      next = listB[j++];
    } else if (j >= listB.length) {
      next = listA[i++];
    } else {
      next = listA[i].at <= listB[j].at ? listA[i++] : listB[j++];
    }
    if (seen.has(next.entry.eventId)) continue;
    seen.add(next.entry.eventId);
    merged.push(next.entry);
  }
  return merged;
}
