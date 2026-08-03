// Whether the signed-in viewer wants facial-recognition data read into the
// surfaces that show it.
//
// SCOPE, stated because the alternative reading is a much stronger promise
// than this keeps: it is a *viewer-side read control over the reporting
// surfaces*, not stored consent. It governs what the browser in front of you
// asks those surfaces for, and each of them honours it by not issuing the
// query. It does NOT stop facial signals being recorded about a student, and
// it does not travel with the student -- a parent switching it off changes
// what their own browser reads, while another viewer with their own switch on
// still sees that child's facial data.
//
// The surfaces it covers, which are the ones that render the switch:
//
//   - the student progress report (weekly-report endpoint, teacher and parent)
//   - the parent dashboard (the children endpoint and the summary RPCs)
//   - the teacher student list (the signal-summary endpoint)
//
// And the ones it does NOT, both teacher-only and neither of which renders the
// switch. Named rather than left implied, because "every surface honours it"
// is the claim a reader will otherwise carry away from this file, and it is
// the claim that would make the control untrustworthy if someone checked:
//
//   - live class monitoring (pages/teacher/Live.jsx via
//     /api/teacher/classes/{id}/live), which shows a live attention gauge, the
//     current emotion and a camera-on badge
//   - session review (pages/teacher/SessionReview.jsx via
//     /api/signals/session/{id}), which plots one finished session's raw
//     facial samples
//
// That is a deliberate boundary rather than an oversight: both are real-time
// or per-session monitoring views built around whether the camera is working,
// which a reporting-window preference has no sensible reading on. Extending
// the control to them is a product decision, not a bug fix -- and it would
// need the switch rendered there, since a control that silently changes a page
// it is absent from is worse than one with a stated edge. Both call sites
// carry a pointer back here so the two halves cannot drift apart.
//
// Making it consent over a child's biometrics would be a different feature: a
// per-student setting held server-side and enforced where face_signals is
// written, not a per-browser preference applied at read time. That is issue
// #47; nothing here should be described as consent until it lands, and this
// comment needs revisiting when it does. The toggle copy in SignalPanel is
// written to match this scope ("this does not switch a camera on or off") --
// keep the two in step.
//
// Shared rather than owned by one component: every reporting surface listed
// above reads facial signals, and a privacy switch that one of them honours
// while a sibling ignores it is worse than no switch -- it implies a guarantee
// the app does not keep. Adding a reporting surface means wiring it to this
// preference and adding it to that list, not assuming it is covered.
//
// Deliberately one setting covering every student rather than one key per
// student. Off means off everywhere; a per-student key would quietly re-enable
// facial data on the next child a parent opened, which is the opposite of what
// someone reaching for this control is asking for.
const FACE_PREF_KEY = 'signal_include_face'

export function readFacePref() {
  try {
    return localStorage.getItem(FACE_PREF_KEY) !== 'false'
  } catch {
    return true   // Safari private mode and friends: default to the normal report.
  }
}

export function writeFacePref(enabled) {
  try {
    localStorage.setItem(FACE_PREF_KEY, enabled ? 'true' : 'false')
  } catch { /* preference is best-effort; the toggle still works this session */ }
}

// Cleared on sign-out. The key is per-browser, not per-account, so without this
// the next person to sign in on a shared machine inherits whichever choice the
// previous one made -- silently switching facial reporting back on for someone
// who never asked for it, or off for someone who did not choose that.
export function clearFacePref() {
  try {
    localStorage.removeItem(FACE_PREF_KEY)
  } catch { /* nothing to clean up if storage is unavailable */ }
}

// The other half of the setting: whether a payload already in hand was built
// with facial data in it. The backend sets face_included=false when the viewer
// opted out, which leaves every face field null -- indistinguishable from "the
// camera recorded nothing" without the flag, and the two must not be rendered
// the same way. Older payloads predate the field, so absent means included.
//
// Lives here rather than beside its first caller because the signal panels, the
// weekly report and the parent dashboard all apply it. One definition, so the
// surfaces cannot drift into disagreeing about what a missing flag means.
export function faceIncluded(payload) {
  return payload?.face_included !== false
}
