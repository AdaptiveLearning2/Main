import { apiFetch } from './api'

/**
 * The backend does the actual sample writing — it polls the EEGResearch
 * sidecar on :8001 and writes to Supabase. This just toggles that polling
 * on/off for the current session.
 *
 * Bringing the stream up and recording are two calls, not one. Pairing needs
 * the poller (it is what starts the sidecar's device stream, and it feeds
 * contact and battery to the page), but a student who paired and never
 * started a question has not had a lesson recorded -- so `start({record:
 * false})` at Connect, and `start({record: true})` on the first question,
 * which arms the running poller in place. Ending the session stops it.
 */
export function createSignalRecorder({ sessionId, deviceId }) {
  let active = false
  let recording = false

  const start = async ({ record = true } = {}) => {
    if (!sessionId) return { ok: false }
    // Already up and already in the asked-for state: nothing to send.
    if (active && recording === record) return { ok: true, running: true, already: true }
    try {
      const res = await apiFetch('/api/eeg/start', {
        method: 'POST', body: { session_id: sessionId, device_id: deviceId, record }
      })
      active = !!res.running
      recording = active && record
      return { ok: true, ...res }
    } catch (e) {
      return { ok: false, error: e.message || String(e) }
    }
  }

  const stop = async () => {
    if (!active || !sessionId) return
    try {
      await apiFetch('/api/eeg/stop', { method: 'POST', body: { session_id: sessionId } })
    } catch {
      // Releasing a stream that has already gone is not a failure worth a toast.
    }
    active = false
    recording = false
  }

  // auto-stop on tab close
  const onUnload = () => { stop() }
  window.addEventListener('beforeunload', onUnload)

  return {
    sessionId,
    start,
    stop: () => { window.removeEventListener('beforeunload', onUnload); return stop() },
    isActive: () => active,
    isRecording: () => recording,
  }
}

export async function eegHealth() {
  try { return await apiFetch('/api/eeg/health') }
  catch (e) { return { available: false, error: e.message } }
}

export async function eegStatus(deviceId) {
  const path = deviceId ? `/api/eeg/status?device_id=${encodeURIComponent(deviceId)}` : '/api/eeg/status'
  try { return await apiFetch(path) }
  catch { return { service: false, poller: { running: false } } }
}

export async function eegDevices() {
  try { return await apiFetch('/api/eeg/devices') }
  catch (e) { return { available: false, devices: [], error: e.message } }
}
