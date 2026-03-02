<script lang="ts">
  import { composeAnswer, submitAnswer } from '../questions';
  import type { QuestionState } from '../questions';
  import { router } from '../router';
  import { toasts } from '../stores/toasts';
  import { markBatchSubmitted, tryClaimSubmit, releaseSubmit } from '../stores/questionState';
  import { requestNotificationPermission } from '../adapters/standalone';
  import QuestionCard from './QuestionCard.svelte';
  import ElapsedTime from './ElapsedTime.svelte';

  interface Props {
    sessionId: string;
    executionId: string;
    executionTitle: string | null;
    agentName: string;
    projectName: string | null;
    batchId: string;
    questions: QuestionState[];
    createdAt: string;
    onsubmitted?: (sessionId: string, batchId: string) => void;
  }

  let { sessionId, executionId, executionTitle, agentName: agentLabel, projectName, batchId, questions, createdAt, onsubmitted }: Props = $props();

  let submitting = $state(false);
  let error: string | null = $state(null);
  let allAnswered = $state(false);

  // Local answer state — avoids mutating the parent's prop
  let answers: string[] = $state(questions.map(() => ''));

  // Reset answers only when a genuinely new question batch arrives (stable batchId),
  // NOT on every poll cycle which creates fresh question array references.
  let prevBatchId = batchId;
  $effect(() => {
    if (batchId !== prevBatchId || questions.length !== answers.length) {
      prevBatchId = batchId;
      answers = questions.map(() => '');
      allAnswered = false;
    }
  });

  function checkAllAnswered() {
    allAnswered = answers.length > 0 && answers.every(a => a.trim().length > 0);
  }

  function handleAnswer(index: number, answer: string) {
    answers[index] = answer;
    checkAllAnswered();
  }

  function buildAnswerQuestions(): QuestionState[] {
    return questions.map((q, i) => ({ ...q, answer: answers[i] }));
  }

  async function handleSubmit() {
    if (!allAnswered || submitting) return;
    if (!tryClaimSubmit(sessionId, batchId)) return;
    submitting = true;
    error = null;
    // Request notification permission synchronously from user gesture (before async boundary)
    requestNotificationPermission();
    try {
      await submitAnswer(sessionId, composeAnswer(buildAnswerQuestions()));
      markBatchSubmitted(sessionId, batchId);
      releaseSubmit(sessionId, batchId);
      toasts.success('Answer submitted');
      onsubmitted?.(sessionId, batchId);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to submit';
      releaseSubmit(sessionId, batchId);
    } finally {
      submitting = false;
    }
  }

  function viewExecution() {
    router.navigate(`/execution/${executionId}`);
  }
</script>

<div class="decision-card">
  <div class="card-header">
    <div class="card-meta">
      <span class="card-title">{executionTitle ?? 'Untitled execution'}</span>
      <span class="card-agent">{agentLabel}{projectName ? ` · ${projectName}` : ''}</span>
      <span class="card-waiting">waiting <ElapsedTime startTime={createdAt} /></span>
    </div>
    <button class="view-link" onclick={viewExecution}>
      View execution &rarr;
    </button>
  </div>

  <div class="card-questions">
    {#each questions as q, i (batchId + ':' + i)}
      <QuestionCard
        question={q.questionText}
        context={q.context}
        options={q.options}
        index={i}
        total={questions.length}
        onanswer={(answer) => handleAnswer(i, answer)}
      />
    {/each}
  </div>

  {#if error}
    <div class="card-error" role="alert">{error}</div>
  {/if}

  <div class="card-actions">
    <button
      class="submit-btn"
      disabled={!allAnswered || submitting}
      onclick={handleSubmit}
    >
      {#if submitting}
        Submitting...
      {:else if questions.length <= 1}
        Submit Answer
      {:else}
        Submit All Answers
      {/if}
    </button>
  </div>
</div>

<style>
  .decision-card {
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    overflow: hidden;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.3);
  }

  .card-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    flex: 1;
  }

  .card-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .card-agent {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    padding: 0.0625rem 0.375rem;
    background: hsl(var(--muted));
    border-radius: 0.25rem;
    flex-shrink: 0;
  }

  .card-waiting {
    font-size: 0.6875rem;
    color: hsl(var(--status-attention));
    flex-shrink: 0;
  }

  .view-link {
    font-size: 0.75rem;
    color: hsl(var(--primary));
    background: none;
    border: none;
    cursor: pointer;
    flex-shrink: 0;
    padding: 0.125rem 0;
  }

  .view-link:hover {
    text-decoration: underline;
  }

  .card-questions {
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .card-error {
    margin: 0 1rem;
    padding: 0.375rem 0.625rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
  }

  .card-actions {
    display: flex;
    justify-content: flex-end;
    padding: 0 1rem 0.75rem;
  }

  .submit-btn {
    padding: 0.5rem 1.25rem;
    border-radius: 0.375rem;
    border: none;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    font-size: 0.8125rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
  }

  .submit-btn:hover:not(:disabled) {
    filter: brightness(1.1);
  }

  .submit-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
