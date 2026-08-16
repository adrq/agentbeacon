<script lang="ts">
  import McpServersSection from './McpServersSection.svelte';
  import ShareTagsSection from './ShareTagsSection.svelte';
  import { escalationNotificationsEnabled, turnCompleteNotificationsEnabled } from '../stores/appState';
  import { requestNotificationPermission } from '../adapters/standalone';

  let permissionState = $state<NotificationPermission>(
    typeof window !== 'undefined' && 'Notification' in window ? Notification.permission : 'default'
  );

  async function handleRequestPermission() {
    await requestNotificationPermission();
    if ('Notification' in window) permissionState = Notification.permission;
  }

  let permissionGranted = $derived(permissionState === 'granted');
</script>

<div class="settings-page">
  <div class="settings-content scroll-thin">
    <h2 class="settings-title">Settings</h2>

    <section id="notifications" class="settings-section">
      <h3 class="section-heading">Notifications</h3>
      <div class="notif-container">
        <div class="notif-permission">
          {#if permissionState === 'default'}
            <button type="button" class="notif-permission-btn" onclick={handleRequestPermission}>
              Enable desktop notifications
            </button>
          {:else if permissionState === 'granted'}
            <span class="notif-status notif-status--granted">Desktop notifications enabled</span>
          {:else}
            <span class="notif-status notif-status--denied">
              Notifications blocked. Enable in browser site settings.
            </span>
          {/if}
        </div>

        <label class="notif-toggle" class:disabled={!permissionGranted}>
          <input
            type="checkbox"
            bind:checked={$escalationNotificationsEnabled}
            disabled={!permissionGranted}
            data-testid="toggle-escalation"
          />
          <span class="notif-toggle-label">Notify on escalation questions</span>
        </label>

        <label class="notif-toggle" class:disabled={!permissionGranted}>
          <input
            type="checkbox"
            bind:checked={$turnCompleteNotificationsEnabled}
            disabled={!permissionGranted}
            data-testid="toggle-turn-complete"
          />
          <span class="notif-toggle-label">Notify when execution turn is complete</span>
        </label>
      </div>
    </section>

    <section id="integrations" class="settings-section">
      <h3 class="section-heading">Integrations</h3>
      <p class="section-description">Manage external tool server connections.</p>
      <McpServersSection />
    </section>

    <section id="wiki-sharing" class="settings-section">
      <h3 class="section-heading">Share Tags</h3>
      <p class="section-description">
        A project admitted to a tag sees every page carrying it, and its own tagged pages
        become visible to the other members.
      </p>
      <ShareTagsSection />
    </section>
  </div>
</div>

<style>
  .settings-page {
    flex: 1;
    display: flex;
    min-height: 0;
    overflow: hidden;
  }

  .settings-content {
    flex: 1;
    overflow-y: auto;
    padding: 1.5rem 2rem;
    min-width: 0;
  }

  .settings-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 1.5rem;
  }

  .settings-section {
    max-width: 720px;
    margin-bottom: 3rem;
  }

  .section-heading {
    font-size: 11px;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.75rem;
  }

  .section-description {
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--muted-foreground));
    margin-bottom: 1rem;
  }

  .notif-container {
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
  }

  .notif-permission {
    margin-bottom: 0.25rem;
  }

  .notif-permission-btn {
    font-size: 13px;
    font-weight: 500;
    padding: 0.375rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.3);
    color: hsl(var(--foreground));
    cursor: pointer;
    transition: border-color 0.15s;
  }

  .notif-permission-btn:hover {
    border-color: hsl(var(--foreground) / 0.3);
  }

  .notif-status {
    font-size: 11px;
    font-weight: 500;
  }

  .notif-status--granted {
    color: hsl(var(--status-healthy));
  }

  .notif-status--denied {
    color: hsl(var(--muted-foreground));
  }

  .notif-toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    cursor: pointer;
  }

  .notif-toggle.disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .notif-toggle input[type="checkbox"] {
    accent-color: hsl(var(--foreground));
    width: 14px;
    height: 14px;
    cursor: inherit;
  }

  .notif-toggle-label {
    font-size: 13px;
    font-weight: 400;
    color: hsl(var(--foreground));
  }

  @media (max-width: 768px) {
    .settings-content {
      padding: 1rem;
    }

    .section-heading {
      position: sticky;
      top: 0;
      background: hsl(var(--background));
      padding: 0.5rem 0;
      z-index: 10;
    }
  }
</style>
