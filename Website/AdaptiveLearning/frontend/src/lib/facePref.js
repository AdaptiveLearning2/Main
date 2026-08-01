// Whether the signed-in viewer wants facial-recognition data read into the
// surfaces that show it.
//
// Shared rather than owned by one component: the student progress report and
// the teacher student list both read facial signals, and a privacy switch that
// one surface honours while a sibling ignores it is worse than no switch --
// it implies a guarantee the app does not keep.
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
