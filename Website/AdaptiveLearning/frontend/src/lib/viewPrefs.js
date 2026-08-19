// Teacher-side display preferences. **Not a privacy control.**
//
// This replaces `facePref.js`, and the rename is the point. That module was a
// viewer-side read filter wearing the vocabulary of consent, and it needed a
// disclaimer sentence in its own UI copy — "this does not switch a camera on or
// off" — to stop being read as one. Needing that sentence was the signal the
// control was wrong. Stored consent now does the job the old switch appeared to
// do, so what is left here is decluttering and is named as such.
//
// Deliberately a new module and a new key. Reusing `signal_include_face` would
// carry the old meaning into the new control, and a browser that still holds
// the old value would silently apply it to something that means something else.
//
// ## What this does
//
// One switch — *"Hide sensor data"* — on the two teacher surfaces that render
// it: the student list (`/teacher/students`) and the student report
// (`/teacher/students/:id/report`). On, the sensor sections are not rendered.
//
// ## What it does not do, and why that is not a bug
//
// **It is client-side only. It fetches the data and then does not draw it.**
// That is a deliberate departure from CLAUDE.md's "the facial opt-out means the
// data is not read" rule, which now belongs to consent — consent genuinely
// skips the query, server-side, and is not the viewer's to override.
//
// The departure is acceptable here because the teacher is already authorised
// for this data by relationship: `_verify_can_view_student` let them have it,
// and hiding it is a preference about screen clutter, not a privacy boundary.
// Stated plainly because a future reader will otherwise find a filter that
// fetches what it hides and reasonably assume it is a bug.
//
// Keeping it client-side also avoids reintroducing a second `include_face`-style
// axis through every reporting endpoint, which is what made the old control
// expensive to reason about: two controls, composed, with consent the only one
// that actually decided anything.
//
// ## Scope
//
// `/teacher/students` and `/teacher/students/:id/report` — the two surfaces
// that render the switch. Live class monitoring (`pages/teacher/Live.jsx`) and
// session review (`pages/teacher/SessionReview.jsx`) are outside it and stay
// that way: those are real-time supervision views built around whether a sensor
// is working, and hiding the data is the wrong default there.
//
// That boundary is the same one `facePref.js` documented, for the same reason:
// a control that silently changes a page it is absent from is worse than one
// with a stated edge. If this is ever extended to those pages, the switch has
// to be rendered there too.
//
// ## What it never does
//
// It does not manufacture a reason. A channel that is off because consent was
// withdrawn still reads "not recorded — turned off on <date>" when the filter
// is off. The filter hides; it never explains.

import { readBoolPref, writePref, clearPref } from './localPref'

const HIDE_SENSOR_DATA_KEY = 'teacher_hide_sensor_data'

export function readHideSensorData() {
  // Defaults to showing: this is a decluttering preference, and the
  // un-decluttered view is the normal one. `readBoolPref` carries the guard --
  // `localStorage` throws rather than returning null in Safari private mode and
  // friends.
  return readBoolPref(HIDE_SENSOR_DATA_KEY, false)
}

export function writeHideSensorData(hidden) {
  writePref(HIDE_SENSOR_DATA_KEY, hidden ? 'true' : 'false')
}

// Cleared on sign-out. The key is per-browser, not per-account, so without this
// the next person to sign in on a shared machine inherits whichever choice the
// previous one made.
export function clearViewPrefs() {
  clearPref(HIDE_SENSOR_DATA_KEY)
}
