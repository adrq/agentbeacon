import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

const beaconPort = parseInt(process.env.AGENTBEACON_PORT || '9456', 10);
const vitePort = parseInt(process.env.VITE_DEV_PORT || '', 10) || (beaconPort + 1000);

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: vitePort,
    proxy: {
      '/api': {
        target: `http://localhost:${beaconPort}`,
        changeOrigin: true,
        // Prevent http-proxy from buffering SSE responses
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache';
              proxyRes.headers['x-accel-buffering'] = 'no';
            }
          });
        },
      },
      '/.well-known': {
        target: `http://localhost:${beaconPort}`,
        changeOrigin: true,
      },
      '/rpc': {
        target: `http://localhost:${beaconPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
