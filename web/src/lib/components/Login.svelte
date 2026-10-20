<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { api, ApiError } from '../api';

  let { onSignedIn }: { onSignedIn: () => void } = $props();

  let username = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let submitting = $state(false);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    submitting = true;
    try {
      await api.login(username, password);
      password = '';
      onSignedIn();
    } catch (e) {
      error = e instanceof ApiError && e.status === 401
        ? 'Invalid username or password.'
        : 'Could not sign in. Try again.';
    } finally {
      submitting = false;
    }
  }
</script>

<div class="login-page">
  <form class="login-card" onsubmit={submit} data-testid="login-form">
    <h1 class="login-title">Sign in to AgentBeacon</h1>
    <label class="login-field">
      <span>Username</span>
      <input
        type="text"
        name="username"
        autocomplete="username"
        bind:value={username}
        required
        data-testid="login-username"
      />
    </label>
    <label class="login-field">
      <span>Password</span>
      <input
        type="password"
        name="password"
        autocomplete="current-password"
        bind:value={password}
        required
        data-testid="login-password"
      />
    </label>
    {#if error}
      <p class="login-error" role="alert" data-testid="login-error">{error}</p>
    {/if}
    <button type="submit" class="login-submit" disabled={submitting} data-testid="login-submit">
      {submitting ? 'Signing in…' : 'Sign in'}
    </button>
  </form>
</div>

<style>
  .login-page {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
  }

  .login-card {
    width: 100%;
    max-width: 22rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.75rem;
    background: hsl(var(--card));
  }

  .login-title {
    font-size: 1.125rem;
    font-weight: 600;
    margin: 0;
  }

  .login-field {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    font-size: 0.875rem;
  }

  .login-field input {
    padding: 0.5rem 0.625rem;
    border: 1px solid hsl(var(--input));
    border-radius: 0.375rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.875rem;
  }

  .login-error {
    margin: 0;
    font-size: 0.8125rem;
    color: hsl(var(--destructive));
  }

  .login-submit {
    padding: 0.5rem 0.75rem;
    border: none;
    border-radius: 0.375rem;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    font-weight: 500;
    cursor: pointer;
  }

  .login-submit:disabled {
    opacity: 0.6;
    cursor: default;
  }
</style>
