import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built files are served by FastAPI at /ui. In dev, API calls are proxied to the API
// (default :8000; override with API_TARGET=http://localhost:8010 npm run dev).
const target = process.env.API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  server: {
    proxy: {
      '/api': { target, rewrite: (p) => p.replace(/^\/api/, '') },
      '/ws': { target: target.replace(/^http/, 'ws'), ws: true },
    },
  },
})
