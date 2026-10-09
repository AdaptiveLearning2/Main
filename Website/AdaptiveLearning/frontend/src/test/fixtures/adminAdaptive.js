// The payload of /api/admin/adaptive, as main.py builds it.

export const ADAPTIVE_PATH = '/api/admin/adaptive?days=14'

export const adaptiveDay = (over = {}) => ({
  day: '2026-10-08', withheld: false, students: 7, decisions: 60, eased: 6, raised: 14, held: 40,
  withheld_increase: 2,
  by_why: { nothing_to_act_on: 30, correct_run: 14, recent_misses: 10, stressed: 6 },
  by_label: { neutral: 50, stressed: 6, no_eeg: 4 },
  ...over,
})

export function buildAdaptive(over = {}) {
  return {
    retrieved: true,
    days: 14,
    since: '2026-09-25',
    timezone: 'America/Chicago',
    min_students: 5,
    decisions: [{ day: '2026-10-07', withheld: true }, adaptiveDay()],
    ...over,
  }
}
