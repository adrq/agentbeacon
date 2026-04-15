<script lang="ts">
  import McpServersSection from './McpServersSection.svelte';
</script>

<div class="settings-page">
  <div class="settings-content scroll-thin">
    <h2 class="settings-title">Settings</h2>

    <section id="integrations" class="settings-section">
      <h3 class="section-heading">Integrations</h3>
      <p class="section-description">Manage external tool server connections.</p>
      <McpServersSection />
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
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 0.25rem;
  }

  .section-description {
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--muted-foreground));
    margin-bottom: 1rem;
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
