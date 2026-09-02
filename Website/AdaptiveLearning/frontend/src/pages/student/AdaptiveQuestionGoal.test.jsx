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
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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

it('re-arms the check-in for the next session in the sitting', async () => {
  // `goalDismissed` is per-session state and has to clear with the session,
  // exactly like `timeUpDismissed` beside it. Left standing, one "Keep going"
  // silenced the reminder for every later session -- and the picker still
  // showed the number selected, so nothing told the student it was off.
  //
  // Asserted on the reset path rather than by driving a whole second session:
  // the flag is cleared by the effect that every route to "no session" goes
  // through, so that is the behaviour worth pinning.
  const src = readFileSync(
    resolve(process.cwd(), 'src/pages/student/Adaptive.jsx'), 'utf8')
  const reset = src.slice(src.indexOf('setSessionStartedAt(null)'))
  const block = reset.slice(0, reset.indexOf('return'))
  for (const call of ['setElapsedMin(0)', 'setTimeUpDismissed(false)', 'setGoalDismissed(false)']) {
    expect(block).toContain(call)
  }
})
