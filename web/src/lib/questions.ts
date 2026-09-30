// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { Event, EscalateData, DataPartPayload } from './types';
import { isMessagePayload, isEscalateData } from './types';
import { api } from './api';
import { isUnsupportedSchema } from './eventSchema';

export interface QuestionState {
  questionText: string;
  context?: string;
  options?: { label: string; description?: string }[];
  answer: string;
}

export interface ExtractedQuestions {
  eventId: string;
  batchId: string;
  questions: QuestionState[];
}

export function extractQuestions(events: Event[]): ExtractedQuestions {
  const empty: ExtractedQuestions = { eventId: '', batchId: '', questions: [] };

  // The last blocking escalation in the list.
  let latest: { data: EscalateData; event: Event } | null = null;
  for (const ev of events) {
    if (isUnsupportedSchema(ev)) continue;
    if (!isMessagePayload(ev.payload)) continue;
    for (const part of ev.payload.parts) {
      if (!('data' in part)) continue;
      const data = part.data as DataPartPayload;
      if (!isEscalateData(data)) continue;
      if ((data as EscalateData).importance !== 'blocking') continue;
      latest = { data: data as EscalateData, event: ev };
    }
  }
  if (!latest) return empty;

  // Look for a matching answer part after it in the list.
  let seenEscalation = false;
  for (const ev of events) {
    if (ev.id === latest.event.id) {
      seenEscalation = true;
      continue;
    }
    if (!seenEscalation) continue;
    if (isUnsupportedSchema(ev)) continue;
    if (!isMessagePayload(ev.payload) || ev.payload.role !== 'ROLE_USER') continue;
    const answered = ev.payload.parts.some((p) => {
      if (!('data' in p)) return false;
      const d = p.data as Record<string, unknown>;
      if (d?.type !== 'question_answer') return false;
      const named = d?.escalation_event_id;
      // Normalize identifier representations before comparison.
      if (named === undefined || named === null) return false;
      return String(named) === latest!.event.id;
    });
    if (answered) return empty;
  }

  return {
    eventId: latest.event.id,
    batchId: latest.data.batch_id,
    questions: (latest.data.questions ?? []).map((q) => ({
      questionText: q.question,
      context: q.context,
      options: q.options,
      answer: '',
    })),
  };
}

export function composeAnswer(questions: QuestionState[]): string {
  if (questions.length === 1) return `${questions[0].questionText}: ${questions[0].answer}`;
  return questions.map(q => `${q.questionText}: ${q.answer}`).join('\n');
}

export async function submitAnswer(
  sessionId: string,
  answer: string,
  escalationEventId: string,
): Promise<void> {
  await api.postMessage(sessionId, [
    { text: answer },
    { data: { type: 'question_answer', escalation_event_id: escalationEventId } },
  ]);
}
