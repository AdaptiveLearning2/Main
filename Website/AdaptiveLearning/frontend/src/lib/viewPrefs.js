// The teacher's "Hide sensor data" preference: client-side decluttering only,
// not a privacy control (consent decides what is recorded or read).
// Scope: teacher student list, student report, and the class cohort panels;
// not live monitoring or session review, and not academic panels.

import { readBoolPref, writePref, clearPref } from './localPref'

const HIDE_SENSOR_DATA_KEY = 'teacher_hide_sensor_data'

export function readHideSensorData() {
  return readBoolPref(HIDE_SENSOR_DATA_KEY, false)
}

export function writeHideSensorData(hidden) {
  writePref(HIDE_SENSOR_DATA_KEY, hidden ? 'true' : 'false')
}

// Cleared on sign-out, for shared machines.
export function clearViewPrefs() {
  clearPref(HIDE_SENSOR_DATA_KEY)
}
