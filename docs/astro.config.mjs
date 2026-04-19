import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
const base = process.env.DOCS_BASE ?? '/docs/';

export default defineConfig({
  integrations: [
    starlight({
      title: 'AgentBeacon Docs',
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
        alt: 'AgentBeacon',
      },
      social: {
        github: 'https://github.com/adrq/agentbeacon',
      },
      sidebar: [
        {
          label: 'Getting Started',
          items: [
            { label: 'Introduction', slug: 'getting-started/introduction' },
            { label: 'Installation', slug: 'getting-started/installation' },
            { label: 'Quick Start', slug: 'getting-started/quickstart' },
          ],
        },
        {
          label: 'Configuration',
          items: [
            { label: 'Agents', slug: 'configuration/agents' },
            { label: 'Drivers', slug: 'configuration/drivers' },
            { label: 'MCP Servers', slug: 'configuration/mcp-servers' },
            { label: 'Storage', slug: 'configuration/storage' },
          ],
        },
      ],
      customCss: ['./src/styles/custom.css'],
    }),
  ],
  base: base,
  outDir: './dist',
  legacy: {
    collections: true,
  },
});
