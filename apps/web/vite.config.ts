import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5175,
      host: true,
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8001',
          changeOrigin: true,
        },
      },
    },
    resolve: {
      alias: [
        {
          find: '@spanel-app',
          replacement: fileURLToPath(new URL('./src/', import.meta.url)),
        },
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
  }
})
