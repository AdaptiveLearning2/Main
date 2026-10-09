// `sessions.close_reason`, as words; `unrecorded` ended before the column existed.
const REASONS = {
  finish: 'Finished by the student',
  sign_out: 'Signed out mid-lesson',
  page_closed: 'Lesson page closed or left',
  student: 'Ended by the student, no reason sent',
  superseded: 'Closed when the student started a new lesson',
  live_stale: 'Closed by Live after going quiet',
  sweep: 'Closed by the sweep as abandoned',
  unrecorded: 'Ended before reasons were recorded',
}

export const reasonLabel = key => REASONS[key] || key
