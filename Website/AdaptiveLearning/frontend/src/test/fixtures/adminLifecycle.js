// Payloads of /api/admin/today and /api/admin/stations, as main.py builds them.

export const TODAY_PATH = '/api/admin/today'
export const STATIONS_PATH = '/api/admin/stations'

export function buildToday(over = {}) {
  return {
    since: '2026-10-08T05:00:00+00:00',
    timezone: 'America/Chicago',
    retrieved: true,
    counts: {
      started: 12, open_now: 3, answers: 214, active_students: 9,
      ended_by_reason: { finish: 5, page_closed: 2, sweep: 1, unrecorded: 1 },
    },
    practice_answers: 36,
    discarded: { page_closed: 2 },
    discarded_retrieved: true,
    ...over,
  }
}

export const poller = (over = {}) => ({
  session_id: 's-ada', user_id: 'u-ada', device_id: 'station1', name: 'Ada', running: true,
  recording: true, withheld: false, page_seen_seconds_ago: 4, page_watching: true,
  samples: 120, errors: 0, ...over,
})

export const station = (over = {}) => ({
  device_id: 'station1',
  pairing: { user_id: 'u-ada', name: 'Ada', session_id: 's-ada', seen_seconds_ago: 4, idle: false },
  pollers: [poller()],
  refused_today: 1,
  last_ended: null,
  ...over,
})

export function buildStations(over = {}) {
  return {
    ingest_mode: 'pull',
    retrieved: true,
    names_retrieved: true,
    refusals_retrieved: true,
    last_ended_retrieved: true,
    idle_after_seconds: 120,
    timezone: 'America/Chicago',
    // A sibling, so a test about one station has another to be wrong about.
    stations: [
      station(),
      station({ device_id: 'station2', refused_today: 0,
                pairing: { user_id: 'u-bo', name: 'Bo', session_id: 's-bo', seen_seconds_ago: 300, idle: true },
                pollers: [poller({ session_id: 's-bo', user_id: 'u-bo', device_id: 'station2', name: 'Bo',
                                   page_seen_seconds_ago: 300, page_watching: false })] }),
    ],
    poller_scope: 'this server process',
    ...over,
  }
}
