import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:9456',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: '../core/cmd/agentmaestro/web/dist',
    emptyOutDir: true
  }
})
