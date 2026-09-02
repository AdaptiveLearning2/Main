/**
 * "How many questions?" is a goal, not a cap.
 *
 * The student picks how many they want this sitting and the page checks in
 * when they get there -- it does not stop them. That mirrors the duration
 * reminder beside it, and for the same stated reason: a session ended on a
 * threshold discards the question a child is part way through answering.
 *
 * So the load-bearing assertions here are the negative ones. Reaching the
 * goal must not end the session, and must not stop questions being served.
 */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: false })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(), stopPush: vi.fn(), stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})), deviceStart: vi.fn(), museRefresh: vi.fn(),
  museConnect: vi.fn(), museDisconnect: vi.fn(), museStatus: vi.fn(async () => ({})),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { endSession } from '../../lib/session'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-goal' }),
    'GET /api/eeg/health': () => ({ available: false }),
  })
})

it('offers a choice of how many questions, and no limit', async () => {
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  for (const label of ['5', '10', '15', '20', 'No limit']) {
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
  }
})

it('says what picking one will do, without promising to stop', async () => {
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  await userEvent.click(screen.getByRole('button', { name: '10' }))
  // "check in", not "stop after" -- the copy has to match the behaviour, or
  // the student is told the session ends when it does not.
  expect(await screen.findByText(/check in after/i)).toBeInTheDocument()
  expect(screen.getByText(/keep going/i)).toBeInTheDocument()
})

it('never ends the session just because a goal was picked', async () => {
  // The whole point of a goal rather than a cap. Nothing about choosing a
  // number may close a session on the student's behalf.
  render(<Adaptive />)
  await screen.findByText(/how many questions/i)
  await userEvent.click(screen.getByRole('button', { name: '5' }))
  expect(endSession).not.toHaveBeenCalled()
})
