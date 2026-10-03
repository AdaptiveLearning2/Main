/** setup.js renders every test inside App.jsx's <LazyMotion>, made strict, so `m` animates here as it does live. */
import { render, screen, waitFor, within } from '@testing-library/react'
import { it, expect, vi } from 'vitest'
// eslint-disable-next-line no-restricted-imports -- `motion` is here to be refused
import { m, motion } from 'framer-motion'

it('fades an m element in, which only the animation features can do', async () => {
  render(<m.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.05 }}>shown</m.p>)
  await waitFor(() => expect(screen.getByText('shown')).toBeVisible())
})

it("keeps the test's own wrapper inside it", () => {
  const Own = ({ children }) => <section aria-label="own">{children}</section>
  render(<m.p>inside</m.p>, { wrapper: Own })
  expect(within(screen.getByRole('region', { name: 'own' })).getByText('inside')).toBeInTheDocument()
})

it('refuses a motion element, as App.jsx does', () => {
  const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    expect(() => render(<motion.p>refused</motion.p>)).toThrow(/rendered a `motion` component within a `LazyMotion`/)
  } finally {
    quiet.mockRestore()
  }
})
