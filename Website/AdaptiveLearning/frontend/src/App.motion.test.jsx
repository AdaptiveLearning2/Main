/** Routes render inside <LazyMotion>: without its features an `m` element never leaves its `initial` style. */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, it, expect, vi } from 'vitest'

vi.mock('./lib/supabase', async () => await import('./test/mocks/supabase'))
vi.mock('./lib/api', async () => await import('./test/mocks/apiFetch'))

import App from './App'
import { resetSupabaseMock } from './test/mocks/supabase'
import { resetApi } from './test/mocks/apiFetch'

beforeEach(() => {
  resetSupabaseMock()
  resetApi()
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
