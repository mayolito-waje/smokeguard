import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        configure(proxy) {
          proxy.on('error', (err) => {
            const msg = err?.message ?? '';
            if (msg.includes('EPIPE') || msg.includes('ECONNRESET')) return;
            if ((err as NodeJS.ErrnoException).code === 'EPIPE') return;
            if ((err as NodeJS.ErrnoException).code === 'ECONNRESET') return;
            console.error('[vite] ws proxy error:', err.message || err);
          });
        },
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
