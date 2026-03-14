import type { Event, EscalateData, DataPartPayload } from './types';
import { isMessagePayload, isEscalateData } from './types';
import { api } from './api';

export interface QuestionState {
  questionText: string;
  context?: string;
  options?: { label: string; description: string }[];
  answer: string;
}

export function extractQuestions(events: Event[]): { batchId: string; questions: QuestionState[] } {
  const escalateEvents: { data: EscalateData; event: Event }[] = [];

  for (const ev of events) {
    if (isMessagePayload(ev.payload)) {
      for (const part of ev.payload.parts) {
        if (part.kind === 'data' && isEscalateData(part.data as DataPartPayload) && (part.data as EscalateData).importance === 'blocking') {
          escalateEvents.push({ data: part.data as EscalateData, event: ev });
        }
      }
    }
  }

  if (escalateEvents.length === 0) return { batchId: '', questions: [] };

  // Group by batch_id, take latest batch
  const batches = new Map<string, typeof escalateEvents>();
  for (const ae of escalateEvents) {
    const batch = batches.get(ae.data.batch_id) ?? [];
    batch.push(ae);
    batches.set(ae.data.batch_id, batch);
  }

  let latestBatchId = '';
  let latestMaxId = -1;
  for (const [batchId, items] of batches) {
    const maxId = Math.max(...items.map(i => i.event.id));
    if (maxId > latestMaxId) {
      latestMaxId = maxId;
      latestBatchId = batchId;
    }
  }

  // Check if the latest batch was already answered (human user message after the ask).
  // Exclude inter-agent messages (distinguished by a sender data part).
  const hasAnswer = events.some(ev => {
    if (ev.id <= latestMaxId) return false;
    if (!isMessagePayload(ev.payload) || ev.payload.role !== 'user') return false;
    const hasSender = ev.payload.parts.some(
      p => p.kind === 'data' && (p.data as Record<string, unknown>)?.type === 'sender'
    );
    return !hasSender;
  });
  if (hasAnswer) return { batchId: '', questions: [] };

  const batch = batches.get(latestBatchId) ?? [];
  batch.sort((a, b) => a.data.batch_index - b.data.batch_index);

  return {
    batchId: latestBatchId,
    questions: batch.map(b => ({
      questionText: b.data.question,
      context: b.data.context,
      options: b.data.options,
      answer: '',
    })),
  };
}

export function composeAnswer(questions: QuestionState[]): string {
  if (questions.length === 1) return questions[0].answer;
  return questions.map(q => `${q.questionText}: ${q.answer}`).join('\n');
}

export async function submitAnswer(sessionId: string, answer: string): Promise<void> {
  await api.postMessage(sessionId, [{ kind: 'text' as const, text: answer }]);
}
