import path from 'node:path'
import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname ?? path.dirname(fileURLToPath(import.meta.url)), './src') },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/events': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: '../web/dist',
    emptyOutDir: true,
  },
})
