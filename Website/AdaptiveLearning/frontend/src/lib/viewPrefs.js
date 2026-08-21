// Teacher-side display preference. Not a privacy control — it only fetches
// and then hides sensor data on screen; consent (server-side) is what
// actually decides whether the data is recorded or read at all.
//
// The teacher is already authorized for this data by relationship, so
// hiding it here is just decluttering, not a privacy boundary. It's kept
// client-side rather than as a second server-side filter to avoid a second
// axis of "should this be included" alongside consent.
//
// Uses its own key (not the old `signal_include_face`) so a browser that
// still has the old value doesn't silently misapply it here.
//
// Scope: only `/teacher/students` and `/teacher/students/:id/report`. Live
// monitoring and session review deliberately don't honor this switch —
// those are real-time views built around whether a sensor is working, where
// hiding data would be the wrong default.
//
// It never invents a reason: a channel off because of consent still shows
// "not recorded — turned off on <date>" regardless of this filter.

import { readBoolPref, writePref, clearPref } from './localPref'

const HIDE_SENSOR_DATA_KEY = 'teacher_hide_sensor_data'

export function readHideSensorData() {
  // Defaults to showing everything — this is decluttering, not a filter
  // that should hide data by default.
  return readBoolPref(HIDE_SENSOR_DATA_KEY, false)
}

export function writeHideSensorData(hidden) {
  writePref(HIDE_SENSOR_DATA_KEY, hidden ? 'true' : 'false')
}

// Cleared on sign-out so the next person on a shared machine doesn't inherit
// this browser's stored choice.
export function clearViewPrefs() {
  clearPref(HIDE_SENSOR_DATA_KEY)
}
