import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const root = process.cwd()
  const env = loadEnv(mode, root, '')
  const backendUrl = env.BACKEND_URL ?? 'http://localhost:8000'

  return {
    plugins: [react()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5173,
      watch: { ignored: [`${root}/dist*/**`, `${root}/playwright-report/**`, `${root}/test-results*/**`] },
      proxy: { '/api': { target: backendUrl, changeOrigin: true } },
    },
    preview: {
      port: 4173,
      proxy: { '/api': { target: backendUrl, changeOrigin: true } },
    },
    build: {
      target: 'es2022',
      sourcemap: 'hidden',
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/shared/config/tests/setup.ts'],
      css: { modules: { classNameStrategy: 'non-scoped' } },
      include: ['src/**/*.test.{ts,tsx}'],
      testTimeout: 30000,
      hookTimeout: 30000,
    },
  }
})
