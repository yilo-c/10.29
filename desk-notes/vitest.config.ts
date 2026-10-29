import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      // Must use array form with RegExp for wildcard prefix matching
      { find: /^@desk-notes\/shared(\/.*)?$/, replacement: path.resolve(__dirname, 'packages/shared/src') + '$1' },
    ],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./packages/desktop/src/test-setup.ts'],
    pool: 'threads',
    singleThread: true,
    css: false,
  },
})
