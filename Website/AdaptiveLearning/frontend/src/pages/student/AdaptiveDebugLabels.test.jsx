/** The debug readout labels the `confidence` key as signal quality, not confidence in the scores. */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// Read at module load in Adaptive.jsx, so it has to be set before the import.
vi.hoisted(() => { vi.stubEnv('VITE_EEG_DEBUG', 'true') })

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  markEegStarted: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => ({ topic: 'ordering' })),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: true, ingest_mode: 'pull' })),
  eegStatus: vi.fn(async () => ({})),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))
vi.mock('../../lib/sidecar', () => ({
  startPush: vi.fn(), stopPush: vi.fn(), stopPushOnUnload: vi.fn(),
  pushStatus: vi.fn(async () => ({})), deviceStart: vi.fn(), museRefresh: vi.fn(),
  museConnect: vi.fn(), museDisconnect: vi.fn(), museStatus: vi.fn(async () => ({})),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarKit: vi.fn(async () => ({ version: null })),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { buildRecordingPermits } from '../../test/fixtures/recordingPermits'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '1st Grade' }),
    'GET /api/recording/me': () => buildRecordingPermits(),
    'GET /api/performance/student/u1': () => [],
    'POST /api/sessions/start': () => ({ id: 'sess-debug' }),
    'GET /api/eeg/health': () => ({ available: true, ingest_mode: 'pull' }),
    'GET /api/eeg/status': () => ({}),
    'GET /api/eeg/debug': () => ({
      available: true,
      muse: { running: true, ingestion: {} },
      snapshot: {
        state: { label: 'neutral' },
        features: { focus_score: 0.5, calm_score: 0.3, confidence: 0.83,
                    signal_quality: 'degraded', quality_basis: 'contact' },
        bands: {},
      },
    }),
  })
})

it('shows the calm score once, as calm, and no derived stress beside it', async () => {
  render(<Adaptive />)
  // One snapshot: the readout re-renders on every debug poll.
  await waitFor(() => {
    // `calm_score` 0.3 is calm 30%; its inverse would be a second name for the same number.
    const readouts = screen.getAllByText(/^calm\b/i).filter(el => /%|—/.test(el.textContent))
    expect(readouts).toHaveLength(1)
    expect(readouts[0]).toHaveTextContent(/^Calm\s*30%$/)
    expect(screen.getByText(/signal quality score/i)).toBeInTheDocument()
    expect(screen.queryByText(/stress/i)).not.toBeInTheDocument()
    expect(screen.queryByText('70%')).not.toBeInTheDocument()
  }, { timeout: 5000 })
})

it('calls the quality number a signal quality score, not confidence', async () => {
  render(<Adaptive />)
  const label = await screen.findByText(/signal quality score/i, {}, { timeout: 5000 })
  expect(label).toHaveTextContent('83%')
  // The word must not survive as a label anywhere on the readout.
  expect(screen.queryByText(/^confidence/i)).not.toBeInTheDocument()
})
