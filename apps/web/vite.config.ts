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
    alias: [
      {
        find: /^@spanel-plugin\/([\w-]+)$/,
        replacement: fileURLToPath(new URL('../../plugins/', import.meta.url)) + '$1/frontend/register',
      },
      {
        find: '@systutor/shell',
        replacement: fileURLToPath(new URL('../../vendor/systutor-shell/src/', import.meta.url)),
      },
    ],
  },
})
