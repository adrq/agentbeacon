<script lang="ts">
  import type { Execution, SessionSummary, Event, Agent } from '../types';
  import { extractQuestions, composeAnswer, submitAnswer } from '../questions';
  import type { QuestionState } from '../questions';
  import QuestionCard from './QuestionCard.svelte';

  interface Props {
    execution: Execution;
    sessions: SessionSummary[];
    events: Event[];
    agents: Agent[];
  }

  let { execution, sessions, events, agents }: Props = $props();

  let submitting = $state(false);
  let submitted = $state(false);
  let error: string | null = $state(null);

  // Derive questions from events but preserve user-typed answers across re-derives
  let questions: QuestionState[] = $state([]);
  let lastBatchId = '';
  let allAnswered = $state(false);

  let inputSessionId = $derived(
    sessions.find(s => s.status === 'input-required')?.id ?? null
  );

  // Update questions only when the batch changes, preserving answers otherwise
  $effect(() => {
    const { batchId: newBatchId, questions: extracted } = extractQuestions(events);
    if (newBatchId !== lastBatchId || extracted.length !== questions.length) {
      lastBatchId = newBatchId;
      questions = extracted;
      allAnswered = false;
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

    submitting = true;
    error = null;

    try {
      await submitAnswer(inputSessionId, composeAnswer(questions));
      submitted = true;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to submit';
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

{#if inputSessionId && !submitted}
  <div class="question-banner">
    <div class="banner-header">
      <span class="banner-icon">&#x26A0;</span>
      <span class="banner-title">
        {#if questions.length === 0}
          LOADING QUESTION...
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
    </div>

    <div class="banner-questions">
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
      <div class="banner-error">{error}</div>
    {/if}

    <div class="banner-actions">
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
{:else if submitted}
  <div class="submitted-banner">
    <span class="submitted-icon">&#x2713;</span>
    Answers submitted. Waiting for agent to resume...
  </div>
{/if}

<style>
  .question-banner {
    margin: 0.75rem 1rem;
    padding: 1rem;
    border: 2px solid hsl(var(--status-attention) / 0.4);
    border-radius: 0.5rem;
    background: hsl(var(--status-attention) / 0.05);
  }

  .banner-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  .banner-icon {
    color: hsl(var(--status-attention));
    font-size: 1rem;
  }

  .banner-title {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: hsl(var(--status-attention));
  }

  .banner-meta {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .banner-questions {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .banner-error {
    margin-top: 0.5rem;
    padding: 0.375rem 0.625rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
  }

  .banner-actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 0.75rem;
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

  .submitted-banner {
    margin: 0.75rem 1rem;
    padding: 0.75rem 1rem;
    border-radius: 0.375rem;
    background: hsl(var(--status-success) / 0.1);
    color: hsl(var(--status-success));
    font-size: 0.8125rem;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .submitted-icon {
    font-size: 1rem;
    font-weight: 700;
  }
</style>
