/** Readings the backend refused under push are counted on the lesson page, not only in the sidecar. */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: null, ingest_mode: 'push' })),
  eegStatus: vi.fn(async () => ({ ingest_mode: 'push', service: null, poller: {} })),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(async () => ({})), stopPush: vi.fn(async () => ({})),
  stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({ enabled: true, running: true, recorded: {} })),
  deviceStart: vi.fn(async () => ({})), deviceStop: vi.fn(async () => ({})),
  deviceStopOnUnload: vi.fn(),
  museRefresh: vi.fn(async () => ({})),
  museConnect: vi.fn(async () => ({})),
  museDisconnect: vi.fn(async () => ({})),
  museState: vi.fn(async () => ({ running: false, ingestion: {} })),
  devices: vi.fn(async () => [{ device_id: 'default', kind: 'muse', running: false }]),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarState: vi.fn(async () => null),
  debugFaceFrame: vi.fn(async () => null),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { pushStatus } from '../../lib/sidecar'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/classes': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-push' }),
    'GET /api/performance/student/u1': () => [],
    'GET /api/generate-question?bias=0&grade=1st+Grade&session_id=sess-push': () => ({
      id: 'q1', question_text: 'What is 2 + 2?', question_topic: 'ordering',
      answer_options: ['3', '4', '5'], correct_answer: '4', difficulty: 'easy',
    }),
  })
})

afterEach(() => cleanup())

/** Render and start a session, which is what starts the push status poll. */
async function renderInSession(status) {
  pushStatus.mockImplementation(async () => status)
  render(<Adaptive />)
  await userEvent.click(await screen.findByRole('button', { name: /generate question/i }))
  await screen.findByText('What is 2 + 2?')
  await screen.findByText(/RECORDING/)
}

it('counts refused and unreadable readings together as not saved', async () => {
  await renderInSession({
    enabled: true, running: true, recorded: { cognitive: 5 },
    rejected: { face: 2, heart: 0, cognitive: 0 }, malformed: { heart: 1 },
  })
  expect(await screen.findByText('3 readings not saved')).toBeInTheDocument()
})

it('says nothing when every reading was saved', async () => {
  await renderInSession({
    enabled: true, running: true, recorded: { cognitive: 5 },
    rejected: { face: 0, heart: 0, cognitive: 0 }, malformed: {},
  })
  expect(screen.queryByText(/not saved/)).not.toBeInTheDocument()
})
