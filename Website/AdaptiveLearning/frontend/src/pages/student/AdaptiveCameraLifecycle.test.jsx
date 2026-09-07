/**
 * A camera switched on from this page is switched off when the page goes.
 *
 * The headband deliberately stays paired across navigation -- the bridge
 * holds the link and re-pairing costs a scan. The webcam has no such cost,
 * and nothing used to stop it: leaving the Adaptive page left the sidecar
 * reading and discarding frames, lens open, until someone came back and
 * pressed Turn off. Two exits, because effect cleanup does not run on a tab
 * close: the route change takes the ordinary stop, `pagehide` the keepalive
 * one.
 */
import { it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
vi.mock('../../lib/session', () => ({
  endSession: vi.fn(async () => true),
  recordAnswer: vi.fn(async () => null),
}))
vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({ available: null, ingest_mode: 'push' })),
  eegStatus: vi.fn(async () => ({ ingest_mode: 'push', service: null, poller: {} })),
  eegDevices: vi.fn(async () => ({ devices: [] })),
}))

// Rewritten per test: whether the sidecar reports the camera capturing, and
// how many device-list reads fail before it answers (a sidecar still
// starting up).
const registry = { cameraRunning: true, failures: 0 }
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
  devices: vi.fn(async () => {
    if (registry.failures > 0) { registry.failures -= 1; throw new Error('sidecar not up yet') }
    return [
      { device_id: 'default', kind: 'muse', running: false },
      { device_id: 'camera', kind: 'face', running: registry.cameraRunning },
    ]
  }),
  releasePushIfIdle: vi.fn(async () => ({ stopped: true, devices: [] })),
  sidecarDebug: vi.fn(async () => ({})),
  sidecarState: vi.fn(async () => null),
  debugFaceFrame: vi.fn(async () => null),
}))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.c' }, role: 'student', loading: false }),
}))

import { deviceStop, deviceStopOnUnload, devices } from '../../lib/sidecar'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  registry.cameraRunning = true
  registry.failures = 0
  mockApi({
    'GET /api/profile/me': () => ({ id: 'u1', role: 'student', grade_level: '4th Grade' }),
    'GET /api/classes': () => [],
    'GET /api/performance/student/u1': () => [],
  })
})

afterEach(() => cleanup())

/** Render, and wait until the page has learned the camera is on. */
async function renderWithCameraOn() {
  const view = render(<Adaptive />)
  await screen.findByText(/on, not recording/i)
  return view
}

it('stops the camera when the page unmounts', async () => {
  const { unmount } = await renderWithCameraOn()
  expect(deviceStop).not.toHaveBeenCalled()
  unmount()
  await waitFor(() => expect(deviceStop).toHaveBeenCalledWith('camera'))
  // The headband is not a camera; it stays paired.
  expect(deviceStop).not.toHaveBeenCalledWith('default')
})

it('stops the camera on pagehide with the keepalive stop, since cleanup never runs there', async () => {
  await renderWithCameraOn()
  window.dispatchEvent(new Event('pagehide'))
  expect(deviceStopOnUnload).toHaveBeenCalledWith('camera')
  expect(deviceStop).not.toHaveBeenCalled()
})

// Real timers: the retry is on a 5 s cadence and this component does not
// survive a fake clock (see AdaptiveReconnect.test.jsx). One retry is enough
// to show the card appears without a reload; the timeout says what it costs.
it('keeps asking for the device list until the sidecar answers, so the camera card appears without a reload', async () => {
  registry.failures = 1
  registry.cameraRunning = false
  render(<Adaptive />)
  // The first read failed; without the retry this never renders.
  await screen.findByRole('button', { name: /turn on camera/i }, { timeout: 9000 })
  expect(devices.mock.calls.length).toBeGreaterThanOrEqual(2)
}, 15_000)

it('sends nothing for a camera that is already off', async () => {
  registry.cameraRunning = false
  const { unmount } = render(<Adaptive />)
  await screen.findByRole('button', { name: /turn on camera/i })
  window.dispatchEvent(new Event('pagehide'))
  unmount()
  // Give any stray async stop a tick to land before asserting it did not.
  await new Promise(r => setTimeout(r, 50))
  expect(deviceStop).not.toHaveBeenCalled()
  expect(deviceStopOnUnload).not.toHaveBeenCalled()
})
