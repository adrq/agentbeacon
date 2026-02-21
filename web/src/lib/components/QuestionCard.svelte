<script lang="ts">
  import type { QuestionOption } from '../types';

  interface Props {
    question: string;
    context?: string;
    options?: QuestionOption[];
    index?: number;
    total?: number;
    answer?: string;
    onanswer?: (answer: string) => void;
  }

  let { question, context, options, index = 0, total = 1, answer = '', onanswer }: Props = $props();

  type Selection = 'option' | 'other' | 'decide' | 'freetext' | '';
  let selectionType: Selection = $state('');
  let selectedOption = $state('');
  let otherText = $state('');
  let freeText = $state('');

  let hasOptions = $derived(options && options.length > 0);

  function updateAnswer() {
    let val = '';
    if (selectionType === 'option') {
      val = selectedOption;
    } else if (selectionType === 'other') {
      val = otherText;
    } else if (selectionType === 'decide') {
      val = 'Decide for me \u2014 I trust your judgment on this.';
    } else if (selectionType === 'freetext') {
      val = freeText;
    }
    answer = val;
    onanswer?.(val);
  }

  function selectOption(label: string) {
    selectionType = 'option';
    selectedOption = label;
    updateAnswer();
  }

  function selectOther() {
    selectionType = 'other';
    updateAnswer();
  }

  function selectDecide() {
    selectionType = 'decide';
    updateAnswer();
  }

  function handleOtherInput() {
    updateAnswer();
  }

  function handleFreeTextInput() {
    selectionType = 'freetext';
    updateAnswer();
  }
</script>

<div class="question-card">
  {#if total > 1}
    <div class="question-label">Question {index + 1} of {total}</div>
  {/if}

  <p class="question-text">{question}</p>

  {#if context}
    <p class="question-context">{context}</p>
  {/if}

  <div class="options-list">
    {#if hasOptions}
      <div class="options-radio-group" role="radiogroup" aria-label="Answer options">
        {#each options ?? [] as opt}
          <button
            class="option-card"
            class:selected={selectionType === 'option' && selectedOption === opt.label}
            role="radio"
            aria-checked={selectionType === 'option' && selectedOption === opt.label}
            onclick={() => selectOption(opt.label)}
          >
            <span class="option-radio">
              {#if selectionType === 'option' && selectedOption === opt.label}
                <span class="radio-fill"></span>
              {/if}
            </span>
            <div class="option-content">
              <span class="option-label">{opt.label}</span>
              <span class="option-desc">{opt.description}</span>
            </div>
          </button>
        {/each}

        <button
          class="option-card"
          class:selected={selectionType === 'other'}
          role="radio"
          aria-checked={selectionType === 'other'}
          onclick={selectOther}
        >
          <span class="option-radio">
            {#if selectionType === 'other'}
              <span class="radio-fill"></span>
            {/if}
          </span>
          <div class="option-content">
            <span class="option-label">Other</span>
            {#if selectionType === 'other'}
              <textarea
                class="other-input"
                placeholder="Type your answer..."
                bind:value={otherText}
                rows="2"
                oninput={handleOtherInput}
                onclick={(e) => e.stopPropagation()}
              ></textarea>
            {:else}
              <span class="option-desc">Provide a custom answer</span>
            {/if}
          </div>
        </button>

        <button
          class="option-card decide-card"
          class:selected={selectionType === 'decide'}
          role="radio"
          aria-checked={selectionType === 'decide'}
          onclick={selectDecide}
        >
          <span class="option-radio">
            {#if selectionType === 'decide'}
              <span class="radio-fill"></span>
            {/if}
          </span>
          <div class="option-content">
            <span class="option-label">Decide for me</span>
            <span class="option-desc">Let the agent choose the best option.</span>
          </div>
        </button>
      </div>
    {:else}
      <textarea
        class="free-text-input"
        placeholder="Type your answer..."
        bind:value={freeText}
        rows="3"
        oninput={handleFreeTextInput}
      ></textarea>

      <button
        class="option-card decide-card"
        class:selected={selectionType === 'decide'}
        onclick={selectDecide}
      >
        <span class="option-radio">
          {#if selectionType === 'decide'}
            <span class="radio-fill"></span>
          {/if}
        </span>
        <div class="option-content">
          <span class="option-label">Decide for me</span>
          <span class="option-desc">Let the agent choose the best option.</span>
        </div>
      </button>
    {/if}
  </div>
</div>

<style>
  .question-card {
    padding: 0.625rem 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.375rem;
    background: hsl(var(--card));
  }

  .question-label {
    font-size: 0.625rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
    margin-bottom: 0.375rem;
  }

  .question-text {
    font-size: 0.8125rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 0.375rem;
    line-height: 1.35;
  }

  .question-context {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    line-height: 1.4;
    margin-bottom: 0.5rem;
  }

  .options-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .options-radio-group {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .option-card {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.25rem;
    background: transparent;
    cursor: pointer;
    text-align: left;
    transition: border-color 0.1s, background 0.1s;
    width: 100%;
  }

  .option-card:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .option-card.selected {
    border-color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.08);
  }

  .option-radio {
    width: 0.875rem;
    height: 0.875rem;
    border-radius: 50%;
    border: 1.5px solid hsl(var(--border));
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 0.0625rem;
  }

  .option-card.selected .option-radio {
    border-color: hsl(var(--primary));
  }

  .radio-fill {
    width: 0.4375rem;
    height: 0.4375rem;
    border-radius: 50%;
    background: hsl(var(--primary));
  }

  .option-content {
    flex: 1;
    min-width: 0;
  }

  .option-label {
    display: block;
    font-size: 0.75rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .option-desc {
    display: block;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.0625rem;
    line-height: 1.35;
  }

  .other-input, .free-text-input {
    width: 100%;
    margin-top: 0.25rem;
    padding: 0.375rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.25rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.75rem;
    resize: vertical;
    font-family: inherit;
  }

  .other-input:focus, .free-text-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
  }

  .decide-card {
    border-style: dashed;
  }
</style>
