import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import AdminFlags from './Flags'

// The consent bypass is the one control here that records signals from students
// who have not agreed. The backend bounds it -- an explicit duration, a cap, and
// an expiry evaluated on every read -- and these are the matching properties on
// the control itself: it cannot be armed by one click, and it never offers
// "indefinitely".

const apiFetch = vi.fn()
vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))

const DEFAULTS = [
  { key: 'strategy_llm_enabled', enabled: false, bypass_until: null, description: 'Model pass.' },
  { key: 'recording_eeg_enabled', enabled: true, bypass_until: null },
  { key: 'recording_heart_enabled', enabled: true, bypass_until: null },
  { key: 'recording_camera_enabled', enabled: true, bypass_until: null },
  { key: 'consent_enforcement_enabled', enabled: true, bypass_until: null },
]

function respond({ flags = DEFAULTS, active = true } = {}) {
  apiFetch.mockImplementation((path, opts) => {
    if (path === '/api/admin/flags' && !opts) {
      return Promise.resolve({ flags, consent_enforcement_active: active })
    }
    if (path === '/api/admin/env-flags') return Promise.resolve({ flags: [] })
    if (path.endsWith('/history')) {
      return Promise.resolve({ retrieved: true, changes: [] })
    }
    if (opts?.method === 'PUT') {
      return Promise.resolve({ flags, consent_enforcement_active: active })
    }
    return Promise.resolve({})
  })
}

const dangerZone = () =>
  screen.getByText('Consent enforcement').closest('div.rounded-2xl')

beforeEach(() => {
  apiFetch.mockReset()
  vi.useRealTimers()
})

it('will not arm the bypass until the consequence is acknowledged', async () => {
  respond()
  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')
  const zone = within(dangerZone())

  const button = zone.getByRole('button', { name: /Bypass consent/ })
  expect(button).toBeDisabled()

  await userEvent.click(zone.getByRole('checkbox'))
  expect(button).toBeEnabled()
})

it('sends the chosen duration, so the backend can bound it', async () => {
  respond()
  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')
  const zone = within(dangerZone())

  await userEvent.selectOptions(zone.getByRole('combobox'), '60')
  await userEvent.click(zone.getByRole('checkbox'))
  await userEvent.click(zone.getByRole('button', { name: /Bypass consent/ }))

  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(
    '/api/admin/flags/consent_enforcement_enabled',
    { method: 'PUT', body: { enabled: false, bypass_minutes: 60 } }))
})

it('offers no way to bypass consent indefinitely', async () => {
  respond()
  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')
  const zone = within(dangerZone())

  const options = zone.getAllByRole('option').map(o => o.textContent)
  expect(options.length).toBeGreaterThan(0)
  for (const label of options) {
    expect(label).toMatch(/^\d+ minutes$/)
  }
  // The cap the backend enforces. A longer option would be refused with a 422
  // a reader could only discover by clicking it.
  const longest = Math.max(...options.map(l => parseInt(l, 10)))
  expect(longest).toBeLessThanOrEqual(240)
})

it('says plainly that students who did not consent are being recorded', async () => {
  // The banner is the only thing standing between "someone left it on" and
  // nobody noticing, so it states the consequence rather than the setting.
  respond({
    active: false,
    flags: DEFAULTS.map(f => f.key === 'consent_enforcement_enabled'
      ? { ...f, enabled: false, bypass_until: '2099-01-01T00:00:00+00:00' }
      : f),
  })
  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')

  const zone = within(dangerZone())
  expect(zone.getByText(/have not consented/i)).toBeInTheDocument()
  expect(zone.getByRole('button', { name: /Re-enable consent enforcement/ }))
    .toBeInTheDocument()
})

it('clears the acknowledgement once enforcement is back on', async () => {
  // Otherwise a tick carries across into a second, unrelated bypass -- the
  // acknowledgement would then be describing a decision nobody made twice.
  //
  // Driven through the real round trip rather than by re-rendering with new
  // props: the panel reads `active` from its own fetch, so a rerender changes
  // nothing and the test would pass without the effect that clears the box.
  let active = true
  apiFetch.mockImplementation((path, opts) => {
    if (path === '/api/admin/env-flags') return Promise.resolve({ flags: [] })
    if (path.endsWith('/history')) return Promise.resolve({ retrieved: true, changes: [] })
    if (opts?.method === 'PUT') active = opts.body.enabled
    const flags = DEFAULTS.map(f => f.key === 'consent_enforcement_enabled'
      ? { ...f, enabled: active } : f)
    return Promise.resolve({ flags, consent_enforcement_active: active })
  })

  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')

  await userEvent.click(within(dangerZone()).getByRole('checkbox'))
  expect(within(dangerZone()).getByRole('checkbox')).toBeChecked()
  await userEvent.click(
    within(dangerZone()).getByRole('button', { name: /Bypass consent/ }))

  // Bypassed: the acknowledgement is gone from the DOM along with the form.
  const reEnable = await within(dangerZone())
    .findByRole('button', { name: /Re-enable consent enforcement/ })
  await userEvent.click(reEnable)

  // Back to enforced, and the box must not still be carrying the last tick.
  const box = await within(dangerZone()).findByRole('checkbox')
  expect(box).not.toBeChecked()
  expect(within(dangerZone()).getByRole('button', { name: /Bypass consent/ }))
    .toBeDisabled()
})

it('re-reads on a timer, because the bypass expires on the clock', async () => {
  // Nothing writes when it lapses, so without polling the page would go on
  // claiming a bypass that ended ten minutes ago.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  respond()
  render(<AdminFlags />)
  await screen.findByText('Consent enforcement')

  const before = apiFetch.mock.calls.filter(c => c[0] === '/api/admin/flags').length
  await vi.advanceTimersByTimeAsync(31_000)
  const after = apiFetch.mock.calls.filter(c => c[0] === '/api/admin/flags').length

  expect(after).toBeGreaterThan(before)
  vi.useRealTimers()
})

it('marks the deployment flags as needing a redeploy', async () => {
  apiFetch.mockImplementation((path, opts) => {
    if (path === '/api/admin/flags' && !opts) {
      return Promise.resolve({ flags: DEFAULTS, consent_enforcement_active: true })
    }
    if (path === '/api/admin/env-flags') {
      return Promise.resolve({ flags: [{ key: 'INGEST_MODE', value: 'pull',
                                         description: 'How signals arrive.',
                                         editable: false }] })
    }
    return Promise.resolve({ retrieved: true, changes: [] })
  })
  render(<AdminFlags />)

  // Rendered as a value with no control, so it cannot read as a switch that
  // silently does nothing.
  const row = (await screen.findByText('INGEST_MODE')).closest('div.rounded-xl')
  expect(within(row).queryByRole('switch')).not.toBeInTheDocument()
  expect(within(row).getByText('pull')).toBeInTheDocument()
})
