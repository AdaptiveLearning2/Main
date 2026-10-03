// From vitest/config so the `test` block is recognised.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { pagesHeadersPlugin } from './pagesHeaders.js'

const FONT_FILE = /\.(woff2?|ttf|otf|eot)$/i

export default defineConfig({
  plugins: [react(), pagesHeadersPlugin()],
  build: {
    // Never a font as a data: URI, which the CSP's `font-src 'self'` blocks; the rest keep Vite's 4 KiB rule.
    assetsInlineLimit: file => (FONT_FILE.test(file) ? false : undefined),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    // No coverage thresholds on purpose: coverage is informational.
  },
})
