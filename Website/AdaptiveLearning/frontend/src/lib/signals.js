import { apiFetch } from './api'

/**
 * Real EEG recorder. Actual sample insertion is done by the BACKEND
 * (it polls the EEGResearch sidecar service on :8001 and writes to Supabase).
 * The frontend just toggles polling on/off for the current session.
 */
export function createSignalRecorder({ sessionId, deviceId }) {
  let active = false

  const start = async () => {
    if (active || !sessionId) return { ok: false }
    try {
      const res = await apiFetch('/api/eeg/start', {
        method: 'POST', body: { session_id: sessionId, device_id: deviceId }
      })
      active = !!res.running
      return { ok: true, ...res }
    } catch (e) {
      return { ok: false, error: e.message || String(e) }
    }
  }

  const stop = async () => {
    if (!active || !sessionId) return
    try {
      await apiFetch('/api/eeg/stop', { method: 'POST', body: { session_id: sessionId } })
    } catch {}
    active = false
  }

  // auto-stop on tab close
  const onUnload = () => { stop() }
  window.addEventListener('beforeunload', onUnload)

  return {
    start,
    stop: () => { window.removeEventListener('beforeunload', onUnload); return stop() },
    isActive: () => active,
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