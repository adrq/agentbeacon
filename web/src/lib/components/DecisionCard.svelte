<script lang="ts">
  import { composeAnswer, submitAnswer } from '../questions';
  import type { QuestionState } from '../questions';
  import { router } from '../router';
  import QuestionCard from './QuestionCard.svelte';

  interface Props {
    sessionId: string;
    executionId: string;
    executionTitle: string | null;
    agentName: string;
    questions: QuestionState[];
    onsubmitted?: (sessionId: string) => void;
  }

  let { sessionId, executionId, executionTitle, agentName: agentLabel, questions, onsubmitted }: Props = $props();

  let submitting = $state(false);
  let error: string | null = $state(null);
  let allAnswered = $state(false);

  // Local answer state — avoids mutating the parent's prop
  let answers: string[] = $state(questions.map(() => ''));

  // Reset answers only when the questions prop actually changes identity
  let prevQuestions = questions;
  $effect(() => {
    if (questions !== prevQuestions) {
      prevQuestions = questions;
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
    submitting = true;
    error = null;
    try {
      await submitAnswer(sessionId, composeAnswer(buildAnswerQuestions()));
      onsubmitted?.(sessionId);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to submit';
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
      <span class="card-title">{executionTitle ?? executionId.slice(0, 8)}</span>
      <span class="card-agent">{agentLabel}</span>
    </div>
    <button class="view-link" onclick={viewExecution}>
      View execution &rarr;
    </button>
  </div>

  <div class="card-questions">
    {#each questions as q, i}
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
    <div class="card-error">{error}</div>
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
    border-radius: 0.5rem;
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
