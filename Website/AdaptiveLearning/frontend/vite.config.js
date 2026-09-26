// From vitest/config so the `test` block is recognised.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { pagesHeadersPlugin } from './pagesHeaders.js'

export default defineConfig({
  plugins: [react(), pagesHeadersPlugin()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    // No coverage thresholds on purpose: coverage is informational.
  },
})
