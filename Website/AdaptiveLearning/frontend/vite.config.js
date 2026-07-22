// defineConfig comes from vitest/config rather than vite so the `test` block
// below is recognised; it is a superset of vite's own defineConfig.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // Components render into a DOM, so jsdom rather than the default node env.
    environment: 'jsdom',
    // describe/it/expect available without importing them in every file.
    globals: true,
    setupFiles: './src/test/setup.js',
    // Tailwind directives are meaningless here and parsing them only costs time.
    css: false,
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    // No coverage thresholds on purpose. `npm run test:coverage` is
    // informational and cannot fail the build: this suite is a floor (two
    // components), and a threshold set to today's number would either be
    // meaninglessly low or block every PR that adds a file. Add one once
    // coverage reflects a deliberate target rather than a starting point.
  },
})
