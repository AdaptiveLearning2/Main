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
// Rewritten per test: the ingest mode, whether the sidecar reports the camera
// capturing, and how many device-list reads fail before one answers (a
// sidecar still starting up). Declared above the factories because `vi.mock`
// is hoisted -- the factories only read it when called.
const registry = { mode: 'push', cameraRunning: true, failures: 0, failureShape: 'error' }

vi.mock('../../lib/signals', () => ({
  createSignalRecorder: () => ({ start: vi.fn(), stop: vi.fn() }),
  eegHealth: vi.fn(async () => ({
    // Under push the backend never probes a sidecar it has no route to.
    available: registry.mode === 'push' ? null : true,
    ingest_mode: registry.mode,
  })),
  eegStatus: vi.fn(async () => ({ ingest_mode: registry.mode, service: null, poller: {} })),
  // The pull-side list. Neither failure shape is a rejection, which is the
  // whole difficulty: `eegDevices` swallows its own error, and the backend
  // answers a plain 200 with `available: false` when its own 1.5 s healthz
  // probe of the sidecar fails.
  eegDevices: vi.fn(async () => {
    if (registry.failures > 0) {
      registry.failures -= 1
      return registry.failureShape === 'down'
        ? { available: false, ingest_mode: 'pull', devices: [] }
        : { available: false, devices: [], error: 'EEG service unreachable' }
    }
    return { available: true, ingest_mode: 'pull',
             devices: [{ device_id: 'station1', kind: 'muse', running: false }] }
  }),
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
import { eegDevices } from '../../lib/signals'
import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import Adaptive from './Adaptive'

beforeEach(() => {
  resetApi()
  vi.clearAllMocks()
  registry.mode = 'push'
  registry.cameraRunning = true
  registry.failures = 0
  registry.failureShape = 'error'
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
  // The failed read must apply nothing. Folded into an empty list it would
  // set `stationId` to `default` -- which enables Connect, and is what a
  // session started in this window would bind its recorder to. Not retrieved
  // is not answered-with-nothing, so Connect stays disabled until a list
  // actually arrives.
  await waitFor(() => expect(devices).toHaveBeenCalledTimes(1))
  expect(screen.getByRole('button', { name: /connect headband/i })).toBeDisabled()
  // The first read failed; without the retry this never renders.
  await screen.findByRole('button', { name: /turn on camera/i }, { timeout: 9000 })
  expect(devices.mock.calls.length).toBeGreaterThanOrEqual(2)
  await waitFor(() => expect(screen.getByRole('button', { name: /connect headband/i })).not.toBeDisabled())
}, 15_000)

// The same property on the pull branch, which is where it is reachable from
// the default deployment: the health check flips `available` on one slow
// probe and re-runs discovery at exactly the moment the devices read is
// likeliest to fail. `eegDevices` swallows, so the failure arrives as a
// resolved payload with `error` -- the branch's `.catch` never fires, and
// reading only that left this case exactly as it was before the retry.
//
// A stale `stationId` of `default` is not cosmetic: it is what
// `armRecording` binds `createSignalRecorder` and `/api/eeg/start` to, so a
// session started in the window records against a station the headband is
// not on. Connect being enabled is the observable that flips.
// Both shapes, because neither is a rejection and they fail differently:
// `error` is the client swallowing its own exception, `available: false` is
// the backend saying it probed the sidecar and got nothing. The second is
// the likelier one -- a slow sidecar times out `/api/eeg/devices`'s healthz
// probe while `/api/eeg/health`'s separate probe succeeds -- and it is what
// makes `available` flip, which is itself what re-runs discovery.
it.each([
  ['the client swallowed its own error', 'error'],
  ['the backend answered 200 with available: false', 'down'],
])('does not fall back to the default station when %s', async (_name, shape) => {
  registry.mode = 'pull'
  registry.failureShape = shape
  registry.failures = 1
  render(<Adaptive />)
  await waitFor(() => expect(eegDevices).toHaveBeenCalledTimes(1))
  const connect = await screen.findByRole('button', { name: /connect headband/i })
  expect(connect).toBeDisabled()
  // The retry lands and the real station arrives, so Connect comes back.
  await waitFor(() => expect(connect).not.toBeDisabled(), { timeout: 9000 })
  expect(eegDevices.mock.calls.length).toBeGreaterThanOrEqual(2)
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
