/** Routes render inside <LazyMotion>: without its features an `m` element never leaves its `initial` style. */
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, it, expect, vi } from 'vitest'
// eslint-disable-next-line no-restricted-imports -- the one `motion` element, which App must survive
import { motion } from 'framer-motion'

vi.mock('./lib/supabase', async () => await import('./test/mocks/supabase'))
vi.mock('./lib/api', async () => await import('./test/mocks/apiFetch'))

const plant = vi.hoisted(() => ({ motion: false }))
vi.mock('./layout/AuthLayout', async importOriginal => {
  const { default: Real } = await importOriginal()
  return { default: () => (plant.motion ? <motion.div>planted</motion.div> : <Real />) }
})

import App from './App'
import { resetSupabaseMock } from './test/mocks/supabase'
import { resetApi } from './test/mocks/apiFetch'

// Testing Library's own render: setup.js's wrapper would supply the features under test.
const { render } = await vi.importActual('@testing-library/react')

beforeEach(() => {
  resetSupabaseMock()
  resetApi()
  plant.motion = false
  window.history.pushState({}, '', '/login')
})

it('fades a routed page in, which only the animation features can do', async () => {
  render(<App />)
  const heading = await screen.findByRole('heading', { name: 'AdaptiveLearning' }, { timeout: 20_000 })
  // AuthLayout's wrapper starts at opacity 0; nothing but an animation moves it.
  const wrapper = heading.closest('[style*="opacity"]')
  expect(wrapper).not.toBeNull()
  await waitFor(() => expect(wrapper.style.opacity).toBe('1'))
}, 40_000)

it('renders a stray `motion` element rather than blanking the app', async () => {
  // The guards are lint:sinks and setup.js's strict wrapper; live, a miss costs bytes, not the page.
  plant.motion = true
  render(<App />)
  expect(await screen.findByText('planted')).toBeInTheDocument()
})
