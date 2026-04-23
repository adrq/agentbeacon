<script lang="ts">
  import { composeAnswer, submitAnswer } from '../questions';
  import type { QuestionState } from '../questions';
  import { router } from '../router';
  import { toasts } from '../stores/toasts';
  import { markBatchSubmitted, tryClaimSubmit, releaseSubmit, refreshDecisions } from '../stores/questionState';
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
    ondismiss?: () => void;
  }

  let { sessionId, executionId, executionTitle, agentName: agentLabel, projectName, batchId, questions, createdAt, onsubmitted, ondismiss }: Props = $props();

  let collapsed = $state(false);
  let submitting = $state(false);
  let error: string | null = $state(null);
  let allAnswered = $state(false);

  let answers: string[] = $state(questions.map(() => ''));

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
    requestNotificationPermission();
    try {
      await submitAnswer(sessionId, composeAnswer(buildAnswerQuestions()), batchId);
      markBatchSubmitted(sessionId, batchId);
      releaseSubmit(sessionId, batchId);
      toasts.success('Answer submitted');
      await refreshDecisions();
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
  <button type="button" class="card-header" onclick={() => collapsed = !collapsed} aria-expanded={!collapsed}>
    <div class="header-row-1">
      <span class="card-waiting">waiting <ElapsedTime startTime={createdAt} /></span>
      <span class="collapse-toggle">
        <span class="collapse-chevron" class:expanded={!collapsed} aria-hidden="true">&#x25B8;</span>
        {collapsed ? 'Expand' : 'Collapse'}
      </span>
    </div>
    <div class="header-row-2">
      <span class="card-title">{executionTitle ?? 'Untitled execution'}</span>
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <span class="view-execution-link" role="link" tabindex="0" onclick={(e: MouseEvent) => { e.stopPropagation(); viewExecution(); }} onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') { e.stopPropagation(); viewExecution(); } }}>
        view execution &rarr;
      </span>
    </div>
  </button>
  {#if collapsed}
    <div class="collapsed-info">
      <span class="collapsed-summary">
        {questions.length} question{questions.length !== 1 ? 's' : ''} pending
      </span>
    </div>
  {/if}

  {#if !collapsed}
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
        type="button"
        class="dismiss-btn"
        onclick={() => { ondismiss?.(); onsubmitted?.(sessionId, batchId); }}
      >
        Dismiss
      </button>
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
  {/if}
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
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.5rem 0.75rem;
    border: none;
    border-bottom: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.3);
    width: 100%;
    text-align: left;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }

  .header-row-1 {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
  }

  .card-waiting {
    font-size: 10px;
    font-family: var(--font-mono, monospace);
    font-variant-numeric: tabular-nums;
    color: hsl(var(--status-attention));
  }

  .header-row-2 {
    width: 100%;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .card-title {
    font-size: 14px;
    font-weight: 600;
    color: hsl(var(--foreground));
    line-height: 1.3;
  }

  .collapse-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .collapse-toggle:hover {
    color: hsl(var(--foreground));
  }

  .collapse-chevron {
    display: inline-block;
    transition: transform 0.15s ease;
    font-size: 0.5rem;
  }

  .collapse-chevron.expanded {
    transform: rotate(90deg);
  }

  .collapsed-info {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.25rem 0.75rem;
    border-bottom: 1px solid hsl(var(--border));
  }

  .collapsed-summary {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .view-execution-link {
    background: none;
    border: none;
    font-size: 11px;
    font-weight: 500;
    color: hsl(var(--primary));
    cursor: pointer;
    padding: 0;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .view-execution-link:hover {
    text-decoration: underline;
  }

  .card-questions {
    padding: 0.5rem 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .card-error {
    margin: 0 1rem;
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.6875rem;
  }

  .card-actions {
    display: flex;
    justify-content: space-between;
    padding: 0 0.75rem 0.5rem;
  }

  .dismiss-btn {
    padding: 0.375rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s;
  }

  .dismiss-btn:hover {
    color: hsl(var(--foreground));
    border-color: hsl(var(--foreground) / 0.3);
  }

  .submit-btn {
    padding: 0.375rem 1rem;
    border-radius: var(--radius);
    border: none;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
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
