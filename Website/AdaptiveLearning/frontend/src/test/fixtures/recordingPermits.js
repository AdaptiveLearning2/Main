/** `GET /api/recording/me`: every channel `permitted` unless named, e.g. `{ camera: 'declined' }`. */
export function buildRecordingPermits(states = {}) {
  const channel = (name) => ({ state: states[name] ?? 'permitted' })
  return { eeg: channel('eeg'), headband_optical: channel('headband_optical'), camera: channel('camera') }
}
