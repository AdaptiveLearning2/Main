import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
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

  // Must agree with the backend, which validates with Python's `ZoneInfo`.
  // Accepting a value the backend then rejects is worse than no check at all.
  // The rows below are known divergences between `Intl` and `ZoneInfo`.

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
    // `Intl` canonicalizes `US/Central` to `America/Chicago` and `GMT` to
    // `UTC`, so this can't be a plain round-trip check.
    expect(isValidTimezone('US/Central')).toBe(true)
    expect(isValidTimezone('GMT')).toBe(true)
  })

  it('accepts a name that legitimately contains a sign', () => {
    // `Etc/GMT-5` is a valid zone name, not an offset string.
    expect(isValidTimezone('Etc/GMT-5')).toBe(true)
  })

  it('accepts the default the form itself loads with', () => {
    // `UTC` is absent from `Intl.supportedValuesOf('timeZone')`, so a naive
    // check against that list would mark a freshly loaded form invalid.
    expect(isValidTimezone('UTC')).toBe(true)
  })

  it('rejects a plausible typo', () => {
    // One transposed letter is enough to deny recording for the whole
    // deployment, which is the case this whole check exists for.
    expect(isValidTimezone('America/Chigago')).toBe(false)
  })

  it('rejects something merely shaped like a zone', () => {
    // A regex over `Area/City` would wrongly accept this; must use the
    // runtime's own resolver instead.
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
    // The backend 422s an unresolvable zone before persisting it, so this
    // check exists to catch the typo before the round trip, not after.
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
