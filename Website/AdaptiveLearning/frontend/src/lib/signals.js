import { apiFetch } from './api'

/**
 * Toggles the backend's pull-mode poller for a session.
 * `start({record: false})` at Connect (stream only), `start({record: true})`
 * on the first question, which arms the running poller in place.
 */
export function createSignalRecorder({ sessionId, deviceId }) {
  let active = false
  let recording = false

  const start = async ({ record = true } = {}) => {
    if (!sessionId) return { ok: false }
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
      // Already gone: not worth a toast.
    }
    active = false
    recording = false
  }

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
  catch (e) {
    // A 429 is a third state (status unknown), never "offline".
    if (e.status === 429) return { refused: true, error: e.message }
    // `answered: false` tells this apart from the backend's own `{available: false}`.
    return { answered: false, available: false, error: e.message }
  }
}

export async function eegStatus(deviceId) {
  const path = deviceId ? `/api/eeg/status?device_id=${encodeURIComponent(deviceId)}` : '/api/eeg/status'
  try { return await apiFetch(path) }
  // `answered: false`: callers must not read `service: false` as an answer.
  catch { return { answered: false, service: false, poller: { running: false } } }
}

export async function eegDevices() {
  try { return await apiFetch('/api/eeg/devices') }
  catch (e) { return { available: false, devices: [], error: e.message } }
}
