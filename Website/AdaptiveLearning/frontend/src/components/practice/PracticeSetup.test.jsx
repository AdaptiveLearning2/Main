import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import PracticeSetup from './PracticeSetup'

const YOUNG_TOPICS = [
  { name: 'ordering', allowed: true },
  { name: 'algebra', allowed: false },
]
const OLD_TOPICS = [
  { name: 'ordering', allowed: true },
  { name: 'algebra', allowed: true },
]

const draw = (onStart = vi.fn()) => {
  const utils = render(<PracticeSetup onStart={onStart} />)
  return { onStart, ...utils }
}

beforeEach(() => {
  resetApi()
  mockApi({
    '/api/profile/me': () => ({ grade_level: '3rd Grade' }),
    'GET /api/topics?grade=3rd%20Grade': () => YOUNG_TOPICS,
    'GET /api/topics?grade=8th%20Grade': () => OLD_TOPICS,
    '/api/practice-sessions': () => [],
    'POST /api/practice-sessions/start': () => ({
      id: 'sess-1', mode: 'test', topics: ['ordering'], difficulty: 'medium',
    }),
  })
})

it('defaults the grade from the profile and greys out a disallowed topic', async () => {
  draw()
  expect(await screen.findByRole('button', { name: /algebra/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /ordering/i })).toBeEnabled()
})

it('clicking a disallowed topic does nothing', async () => {
  const { onStart } = draw()
  const algebra = await screen.findByRole('button', { name: /algebra/i })
  await userEvent.click(algebra)
  await userEvent.click(screen.getByRole('button', { name: /ordering/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(onStart).toHaveBeenCalled()
  expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/start', expect.objectContaining({
    body: expect.objectContaining({ topics: ['ordering'] }),
  }))
})

it('re-checks which topics are allowed when the grade changes, dropping a now-disallowed selection', async () => {
  draw()
  await screen.findByRole('button', { name: /algebra/i })

  await userEvent.selectOptions(screen.getByLabelText(/grade/i), '8th Grade')

  // algebra is allowed at 8th grade now
  expect(await screen.findByRole('button', { name: /algebra/i })).toBeEnabled()
})

it('picking a topic that becomes allowed does not resurrect a stale disabled state', async () => {
  draw()
  await screen.findByRole('button', { name: /ordering/i })
  await userEvent.click(screen.getByRole('button', { name: /ordering/i }))

  await userEvent.selectOptions(screen.getByLabelText(/grade/i), '8th Grade')
  await screen.findByRole('button', { name: /algebra/i, disabled: false })

  await userEvent.click(screen.getByRole('button', { name: /algebra/i }))
  await userEvent.click(screen.getByRole('button', { name: /start practice/i }))

  expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/start', expect.objectContaining({
    body: expect.objectContaining({ topics: ['ordering', 'algebra'], grade: '8th Grade' }),
  }))
})
