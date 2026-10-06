// `GET /api/recording/me` as `my_recording_permits` builds it, verdicts and wording included.
const REASONS = {
  declined: { eeg: 'eeg not consented', headband_optical: 'headband heart sensor not consented',
              camera: 'camera not consented' },
  switched_off: 'recording is switched off by an administrator',
  school_year_not_started: 'recording has not started for this school year',
  school_year_ended: 'the school year has ended',
  school_year_unconfigured: 'no school year is configured, so nothing is recorded',
}
const SENSORS = { camera: ['camera'], headband: ['eeg', 'headband_optical'] }
const refuses = (state) => state !== 'permitted' && state !== 'unknown'

/** Every channel `permitted` unless named, e.g. `{ camera: 'declined' }`. */
export function buildRecordingPermits(states = {}) {
  const state = (c) => states[c] ?? 'permitted'
  const reason = (c) => {
    const found = REASONS[state(c)]
    return typeof found === 'object' ? found[c] : (found ?? null)
  }
  const channel = (c) => ({ state: state(c), reason: reason(c) })
  return {
    eeg: channel('eeg'), headband_optical: channel('headband_optical'), camera: channel('camera'),
    sensors: Object.fromEntries(Object.entries(SENSORS).map(([name, channels]) => [name, {
      allowed: channels.some(c => state(c) === 'permitted'),
      refused: channels.every(c => refuses(state(c))),
    }])),
    heart_sources: { muse_optics: 'headband_optical', muse_ppg: 'headband_optical', rppg: 'camera' },
  }
}
