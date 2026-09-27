import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import AdminSchoolYear from './SchoolYear'
import { isValidTimezone } from '../../lib/timezone'

const WINDOW = {
  state: 'open', enforced: true,
  starts_on: '2026-09-01', ends_on: '2027-07-20',
  timezone: 'America/Chicago',
}

beforeEach(() => {
  resetApi()
  mockApi({
    '/api/admin/retention-window': () => WINDOW,
    'PUT /api/admin/retention-window': () => WINDOW,
  })
})

describe('isValidTimezone', () => {
  it('accepts a real IANA zone', () => {
    expect(isValidTimezone('America/Chicago')).toBe(true)
    expect(isValidTimezone('UTC')).toBe(true)
  })

  // Must agree with the backend's `ZoneInfo`; rows below are known `Intl` divergences.

  it('rejects a UTC offset, which Intl accepts and ZoneInfo does not', () => {
    expect(isValidTimezone('+00:00')).toBe(false)
    expect(isValidTimezone('+05:30')).toBe(false)
    expect(isValidTimezone('-08:00')).toBe(false)
  })

  it('rejects the wrong case, because the string is saved verbatim', () => {
    // `ZoneInfo` does a case-sensitive path lookup in the tz database.
    expect(isValidTimezone('america/chicago')).toBe(false)
    expect(isValidTimezone('AMERICA/CHICAGO')).toBe(false)
  })

  it('still accepts a legacy alias, which ZoneInfo also accepts', () => {
    // `Intl` canonicalizes aliases, so this cannot be a round-trip check.
    expect(isValidTimezone('US/Central')).toBe(true)
    expect(isValidTimezone('GMT')).toBe(true)
  })

  it('accepts a name that legitimately contains a sign', () => {
    // `Etc/GMT-5` is a valid zone name, not an offset string.
    expect(isValidTimezone('Etc/GMT-5')).toBe(true)
  })

  it('accepts the default the form itself loads with', () => {
    // `UTC` is absent from `Intl.supportedValuesOf('timeZone')`.
    expect(isValidTimezone('UTC')).toBe(true)
  })

  it('rejects a plausible typo', () => {
    // One typo denies recording for the whole deployment.
    expect(isValidTimezone('America/Chigago')).toBe(false)
  })

  it('rejects something merely shaped like a zone', () => {
    // A regex over `Area/City` would accept this; use the runtime's resolver.
    expect(isValidTimezone('Area/Nonsense')).toBe(false)
  })

  it('rejects empty and non-strings without throwing', () => {
    expect(isValidTimezone('')).toBe(false)
    expect(isValidTimezone(null)).toBe(false)
    expect(isValidTimezone(undefined)).toBe(false)
  })
})

describe('the timezone field', () => {
  it('refuses to save a zone the platform cannot resolve', async () => {
    // The backend 422s too; this catches the typo before the round trip.
    render(<AdminSchoolYear />)
    const field = await screen.findByLabelText(/timezone/i)

    await userEvent.clear(field)
    await userEvent.type(field, 'America/Chigago')

    expect(await screen.findByText(/stop recording for every student/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })

  it('marks the field invalid for a screen reader too, not only in colour', async () => {
    render(<AdminSchoolYear />)
    const field = await screen.findByLabelText(/timezone/i)

    await userEvent.clear(field)
    await userEvent.type(field, 'nonsense')

    await waitFor(() => expect(field).toHaveAttribute('aria-invalid', 'true'))
  })

  it('saves a valid one', async () => {
    render(<AdminSchoolYear />)
    const field = await screen.findByLabelText(/timezone/i)

    await userEvent.clear(field)
    await userEvent.type(field, 'Europe/London')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/api/admin/retention-window',
        expect.objectContaining({
          method: 'PUT',
          body: expect.objectContaining({ timezone: 'Europe/London' }),
        })))
  })

  it('leaves the save button alone while the zone is fine', async () => {
    // Confirms the guard isn't just "refuse everything".
    render(<AdminSchoolYear />)
    await screen.findByLabelText(/timezone/i)
    expect(screen.getByRole('button', { name: /save/i })).toBeEnabled()
    expect(screen.queryByText(/stop recording for every student/i)).not.toBeInTheDocument()
  })
})

describe('dates that let the nightly delete reach further', () => {
  const REFUSAL = 'Today is outside these dates, so saving them lets the nightly job delete ' +
    'per-sample signal rows up to 2027-08-31; that cannot be undone.'

  it('shows the refusal and saves only on a second, explicit press', async () => {
    const bodies = []
    mockApi({
      '/api/admin/retention-window': () => WINDOW,
      'PUT /api/admin/retention-window': (_p, opts) => {
        bodies.push(opts.body)
        if (!opts.body.confirm_expiry) throw apiError(409, REFUSAL)
        return WINDOW
      },
    })
    render(<AdminSchoolYear />)
    await screen.findByLabelText(/timezone/i)

    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be undone')
    expect(screen.queryByText(/^Saved\.$/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /save anyway/i }))
    expect(await screen.findByText(/^Saved\.$/)).toBeInTheDocument()
    expect(bodies.map(b => b.confirm_expiry)).toEqual([false, true])
  })

  it('withdraws the offer when a field changes, so a confirmation cannot carry over', async () => {
    mockApi({
      '/api/admin/retention-window': () => WINDOW,
      'PUT /api/admin/retention-window': () => { throw apiError(409, REFUSAL) },
    })
    render(<AdminSchoolYear />)
    await screen.findByLabelText(/timezone/i)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await screen.findByRole('alert')

    await userEvent.clear(screen.getByLabelText(/starts on/i))
    await waitFor(() => expect(screen.queryByRole('button', { name: /save anyway/i })).not.toBeInTheDocument())
  })
})

describe('the Saved message', () => {
  it('clears as soon as a field is edited', async () => {
    // Otherwise "Saved." could keep showing over an unsaved draft.
    render(<AdminSchoolYear />)
    const field = await screen.findByLabelText(/timezone/i)

    await userEvent.click(screen.getByRole('button', { name: /save/i }))
    expect(await screen.findByText(/^Saved\.$/)).toBeInTheDocument()

    await userEvent.type(field, 'x')
    await waitFor(() => expect(screen.queryByText(/^Saved\.$/)).not.toBeInTheDocument())
  })

  it('clears when a date is edited too, not only the timezone', async () => {
    render(<AdminSchoolYear />)
    await screen.findByLabelText(/timezone/i)
    await userEvent.click(screen.getByRole('button', { name: /save/i }))
    await screen.findByText(/^Saved\.$/)

    await userEvent.clear(screen.getByLabelText(/starts on/i))
    await waitFor(() => expect(screen.queryByText(/^Saved\.$/)).not.toBeInTheDocument())
  })
})
