import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const backendPort = process.env.TAURI_ENV ? '18000' : '8000'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/zrender/')) return 'intelligence-chart-renderer'
          if (id.includes('/node_modules/echarts-for-react/')) return 'intelligence-chart-react'
          if (
            id.includes('/node_modules/echarts/lib/chart/')
            || id.includes('/node_modules/echarts/lib/component/')
            || id.includes('/node_modules/echarts/charts')
            || id.includes('/node_modules/echarts/components')
          ) return 'intelligence-chart-features'
          if (id.includes('/node_modules/echarts/')) return 'intelligence-chart-engine'
        },
      },
    },
  },
  server: {
    port: 5173,
    headers: {
      'Cache-Control': 'no-store',
    },
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform';
              proxyRes.headers['x-accel-buffering'] = 'no';
              delete proxyRes.headers['content-length'];
            }
          });
        },
      },
      '/ws': {
        target: `ws://localhost:${backendPort}`,
        ws: true,
      },
    },
  },
})
