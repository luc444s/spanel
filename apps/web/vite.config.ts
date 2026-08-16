import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    host: true,
  },
  resolve: {
    alias: {
      '@systutor/shell': fileURLToPath(new URL('../../vendor/systutor-shell/src/', import.meta.url)),
    },
  },
})
