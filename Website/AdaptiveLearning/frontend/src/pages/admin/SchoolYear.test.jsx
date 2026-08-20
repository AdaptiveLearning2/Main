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

  // The point of this check is to agree with the backend, which validates with
  // Python's `ZoneInfo` and 422s before persisting. Accepting what it then
  // rejects is worse than no check: it tells an admin their value is fine and
  // the save fails anyway, which reads as a broken form rather than a bad zone.
  // Both rows below are measured divergences between `Intl` and `ZoneInfo`.

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
    // The case test cannot be a plain round-trip: `Intl` canonicalises
    // `US/Central` to `America/Chicago` and `GMT` to `UTC`, and rejecting
    // anything that does not round-trip exactly would block valid saves.
    expect(isValidTimezone('US/Central')).toBe(true)
    expect(isValidTimezone('GMT')).toBe(true)
  })

  it('accepts a name that legitimately contains a sign', () => {
    // `Etc/GMT-5` is not an offset string and both sides take it, so the
    // offset test has to anchor at the start rather than search for a sign.
    expect(isValidTimezone('Etc/GMT-5')).toBe(true)
  })

  it('accepts the default the form itself loads with', () => {
    // `UTC` is absent from `Intl.supportedValuesOf('timeZone')`, so validating
    // against that list would mark a freshly loaded form invalid and disable
    // its own Save button.
    expect(isValidTimezone('UTC')).toBe(true)
  })

  it('rejects a plausible typo', () => {
    // The case that motivates all of this: one transposed letter, and
    // `_retention_window()` answers `unreadable`, which denies recording for
    // the whole deployment.
    expect(isValidTimezone('America/Chigago')).toBe(false)
  })

  it('rejects something merely shaped like a zone', () => {
    // A regex over `Area/City` would accept this, which is exactly the failure
    // being prevented -- so the check has to be the runtime's own resolver.
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
    // `admin_set_retention_window` validates with `ZoneInfo` and 422s *before
    // persisting*, so a bad zone is never stored and recording never stops.
    // This check exists to say so before the round trip, not to stand between a
    // typo and an outage -- which makes agreeing with the backend its whole job.
    //
    // (An earlier version of this comment said the field was a platform-wide
    // off switch. That was wrong about the backend, and is corrected here
    // rather than deleted, because the same claim reached a PR description.)
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
    // The guard must not be "refuse everything", which every test above would
    // also pass against.
    render(<AdminSchoolYear />)
    await screen.findByLabelText(/timezone/i)
    expect(screen.getByRole('button', { name: /save/i })).toBeEnabled()
    expect(screen.queryByText(/stop recording for every student/i)).not.toBeInTheDocument()
  })
})

describe('the Saved message', () => {
  it('clears as soon as a field is edited', async () => {
    // It survived any edit, so the form could show "Saved." over a draft that
    // was not -- reporting an unsaved change as persisted, on the form that
    // decides whether recording happens at all.
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
