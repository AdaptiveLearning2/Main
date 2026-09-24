/** A session made only to reserve the headband is ended on leave, so the backend discards it if empty. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
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
import { runSignOutTasks } from '../../lib/signOutTasks'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'POST /api/sessions/start': () => ({ id: 'sess-phantom' }),
    'GET /api/eeg/health': () => ({ available: false }),
    // An array: the page iterates it, and an object throws after the test body.
    'GET /api/performance/student/u1': () => [],
    // The router matches the whole path, query included.
    'GET /api/generate-question?user_id=u1&bias=0&grade=1st+Grade&session_id=sess-phantom': () => ({
      id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
      answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
    }),
  })
})

it('does not end a session that was never created', () => {
  // StrictMode's double-mount unmounts before any session exists.
  const { unmount } = render(<Adaptive />)
  unmount()
  expect(endSession).not.toHaveBeenCalled()
})


it('ends the session when the student leaves, recorded or not', async () => {
  // `/end` deletes an empty session and properly closes one with answers.
  const { unmount } = render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  unmount()
  await waitFor(() => expect(endSession).toHaveBeenCalledWith('sess-phantom'))
})


it('ends the session before sign-out takes the token, and only once', async () => {
  // Sign-out clears the session first and navigation unmounts the page
  // after, so the unmount's `/end` went out with no bearer, 401'd, and left
  // the session open -- under pull, the poller still recording and holding
  // the headband. The sign-out tasks run while the token exists.
  const { unmount } = render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')

  await runSignOutTasks()
  expect(endSession).toHaveBeenCalledWith('sess-phantom')

  unmount()
  expect(endSession).toHaveBeenCalledTimes(1)
})
