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
    // The backend denies rather than falling back to UTC -- deliberately,
    // because a fallback moves every term boundary by hours while looking like
    // it worked. That makes this field a platform-wide off switch, so the form
    // has to catch it rather than the status line afterwards.
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
