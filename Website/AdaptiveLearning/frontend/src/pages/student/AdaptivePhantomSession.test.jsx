/**
 * A session created only to reserve the headband is not one the student
 * started.
 *
 * `toggleHeadband` has to create a session: under `INGEST_MODE=pull` the EEG
 * reservation is scoped by `session_id`, so connecting needs one to hang off.
 * But a student who connects a headband and walks away has not practised, and
 * the row sat in History as a 0-question "Adaptive Session" until the 6-hour
 * sweep collected it -- which is what "it recorded a session I never started"
 * looks like from the outside.
 *
 * Ending it on the way out hands it to `_discard_if_nothing_recorded`, which
 * *deletes* a session that recorded nothing rather than stamping it closed.
 */
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
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'POST /api/sessions/start': () => ({ id: 'sess-phantom' }),
    'GET /api/eeg/health': () => ({ available: false }),
    'GET /api/performance/student/u1': () => ({ topics: [] }),
    // The router matches the whole path, query included.
    'GET /api/generate-question?user_id=u1&bias=0&grade=1st+Grade&session_id=sess-phantom': () => ({
      id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
      answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
    }),
  })
})

it('does not end a session that was never created', () => {
  // Merely visiting the page must not call `/end` on nothing -- and under
  // StrictMode the unmount runs on the dev double-mount, before any session
  // could exist.
  const { unmount } = render(<Adaptive />)
  unmount()
  expect(endSession).not.toHaveBeenCalled()
})


it('ends a session that recorded nothing when the student leaves', async () => {
  // The phantom: a session exists (created to fetch a question or to reserve
  // the headband) but no answer was ever given. Ending it hands it to
  // `_discard_if_nothing_recorded`, which deletes it -- so it never appears
  // in History as a session the student did not start.
  const { unmount } = render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  unmount()
  await waitFor(() => expect(endSession).toHaveBeenCalledWith('sess-phantom'))
})
