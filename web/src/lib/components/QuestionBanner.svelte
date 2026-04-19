<script lang="ts">
  import type { Execution, SessionSummary, Event, Agent } from '../types';
  import { extractQuestions, composeAnswer, submitAnswer } from '../questions';
  import type { QuestionState } from '../questions';
  import { submittedBatches, suppressedSessions, suppressSession, markBatchSubmitted, tryClaimSubmit, releaseSubmit } from '../stores/questionState';
  import { requestNotificationPermission } from '../adapters/standalone';
  import QuestionCard from './QuestionCard.svelte';

  interface Props {
    execution: Execution;
    sessions: SessionSummary[];
    events: Event[];
    agents: Agent[];
  }

  let { execution, sessions, events, agents }: Props = $props();

  let collapsed = $state(false);
  let submitting = $state(false);
  let submitted = $state(false);
  let error: string | null = $state(null);

  // Derive questions from events but preserve user-typed answers across re-derives
  let questions: QuestionState[] = $state([]);
  let lastBatchId = $state('');
  let allAnswered = $state(false);

  // Submit answers to the root session
  let inputSessionId = $derived(
    sessions.find(s => !s.parent_session_id && !s.outcome && s.desired !== 'terminate')?.id ?? null
  );

  // Detect cross-surface submission (e.g., answered from ActionPanel)
  let crossSubmitted = $derived(
    inputSessionId ? $submittedBatches[inputSessionId] === lastBatchId && lastBatchId !== '' : false
  );

  // Detect cross-surface dismiss (e.g., dismissed from DecisionQueue sidebar)
  let isSuppressed = $derived(
    inputSessionId ? $suppressedSessions[inputSessionId] === lastBatchId && lastBatchId !== '' : false
  );

  // Update questions only when the batch changes, preserving answers otherwise
  $effect(() => {
    const { batchId: newBatchId, questions: extracted } = extractQuestions(events);
    if (newBatchId !== lastBatchId || extracted.length !== questions.length) {
      if (newBatchId !== lastBatchId) {
        collapsed = false;
      }
      lastBatchId = newBatchId;
      questions = extracted;
      allAnswered = false;
      submitted = false;
    }
  });

  // Reset submitted state when input session changes
  let prevInputSessionId: string | null = null;
  $effect(() => {
    if (inputSessionId !== prevInputSessionId) {
      prevInputSessionId = inputSessionId;
      submitted = false;
      allAnswered = false;
      error = null;
      lastBatchId = '';
      questions = [];
    }
  });

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  async function handleSubmit() {
    if (!inputSessionId || !allAnswered || submitting) return;
    if (!tryClaimSubmit(inputSessionId, lastBatchId)) return;

    submitting = true;
    error = null;
    requestNotificationPermission();

    try {
      await submitAnswer(inputSessionId, composeAnswer(questions), lastBatchId);
      markBatchSubmitted(inputSessionId, lastBatchId);
      releaseSubmit(inputSessionId, lastBatchId);
      submitted = true;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to submit';
      releaseSubmit(inputSessionId, lastBatchId);
    } finally {
      submitting = false;
    }
  }

  function checkAllAnswered() {
    allAnswered = questions.length > 0 && questions.every(q => q.answer.trim().length > 0);
  }

  function handleAnswer(index: number, answer: string) {
    questions[index].answer = answer;
    checkAllAnswered();
  }
</script>

{#if inputSessionId && !submitted && !crossSubmitted && !isSuppressed && (questions.length > 0 || events.length === 0)}
  <div class="question-banner" class:loading={questions.length === 0}>
    <button type="button" class="banner-header" onclick={() => collapsed = !collapsed} aria-expanded={!collapsed} aria-controls={!collapsed ? "question-content" : undefined}>
      <span class="banner-icon" aria-hidden="true">&#x26A0;</span>
      <span class="banner-title">
        {#if questions.length === 0}
          Loading question&hellip;
        {:else if questions.length <= 1}
          QUESTION
        {:else}
          {questions.length} QUESTIONS
        {/if}
      </span>
      <span class="banner-meta">
        {#if inputSessionId}
          from {agentName(sessions.find(s => s.id === inputSessionId)?.agent_id ?? '')}
        {/if}
        {#if execution.title}&middot; {execution.title}{/if}
      </span>
      <span class="collapse-toggle">
        <span class="collapse-chevron" class:expanded={!collapsed} aria-hidden="true">&#x25B8;</span>
        {collapsed ? 'Expand' : 'Collapse'}
      </span>
      {#if collapsed}
        <span class="collapsed-summary">
          {questions.length} question{questions.length !== 1 ? 's' : ''} pending — click to expand
        </span>
      {/if}
    </button>

    {#if !collapsed}
    <div id="question-content">
      <div class="banner-questions">
        {#each questions as q, i (lastBatchId + ':' + i)}
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
        <div class="banner-error" role="alert">{error}</div>
      {/if}

      <div class="banner-actions">
        <button
          type="button"
          class="dismiss-btn"
          onclick={() => { if (inputSessionId) suppressSession(inputSessionId, lastBatchId); }}
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
    </div>
    {/if}
  </div>
{:else if submitted || crossSubmitted}
  <div class="submitted-banner">
    <span class="submitted-icon">&#x2713;</span>
    Answers submitted. Waiting for agent to resume...
  </div>
{/if}

<style>
  .question-banner {
    margin: 0.5rem 0.75rem;
    padding: 0.625rem 0.75rem;
    border: 1.5px solid hsl(var(--status-attention) / 0.35);
    border-radius: var(--radius);
    background: hsl(var(--status-attention) / 0.10);
  }

  .banner-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    margin-bottom: 0.5rem;
    background: none;
    border: none;
    padding: 0;
    width: 100%;
    text-align: left;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }

  .banner-icon {
    color: hsl(var(--status-attention));
    font-size: 0.8125rem;
  }

  .banner-title {
    font-size: 0.6875rem;
    font-weight: 500;
    letter-spacing: 0.05em;
    color: hsl(var(--status-attention));
  }

  .banner-meta {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .banner-questions {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 50vh;
    overflow-y: auto;
  }

  .collapse-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    margin-left: auto;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    font-weight: 500;
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

  .collapsed-summary {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    width: 100%;
    flex-basis: 100%;
  }

  .banner-error {
    margin-top: 0.375rem;
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.6875rem;
  }

  .banner-actions {
    display: flex;
    justify-content: space-between;
    margin-top: 0.5rem;
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

  .submitted-banner {
    margin: 0.5rem 0.75rem;
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius);
    background: hsl(var(--status-success) / 0.1);
    color: hsl(var(--status-success));
    font-size: 0.6875rem;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 0.375rem;
  }

  .question-banner.loading {
    border-color: hsl(var(--muted-foreground) / 0.2);
    background: hsl(var(--muted-foreground) / 0.03);
  }

  .question-banner.loading .banner-icon,
  .question-banner.loading .banner-title {
    color: hsl(var(--muted-foreground));
  }

  .submitted-icon {
    font-size: 0.8125rem;
    font-weight: 500;
  }
</style>
