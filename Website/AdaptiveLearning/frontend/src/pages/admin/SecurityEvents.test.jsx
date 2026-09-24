import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import AdminSecurityEvents from './SecurityEvents'

const KINDS = ['authz_denied', 'admin_denied', 'rate_limited', 'consent_changed']

const event = (over = {}) => ({
  id: 1,
  kind: 'authz_denied',
  actor: { id: 'teacher-1', name: 'Mr Vance' },
  subject: { id: 'student-1', name: 'Ada' },
  detail: { check: 'can_view_student' },
  created_at: '2026-09-19T10:00:00Z',
  ...over,
})

const payload = (over = {}) => ({
  retrieved: true, names_retrieved: true, kinds: KINDS, events: [event()], ...over,
})

beforeEach(() => {
  resetApi()
  mockApi({ '/api/admin/security-events': () => payload() })
})

describe('the three states it has to keep apart', () => {
  it('shows what happened, in words rather than column names', async () => {
    render(<AdminSecurityEvents />)

    // Scoped to the row: "Access refused" is also a chip, and findBy retries a double match to timeout.
    const row = await screen.findByRole('listitem')

    // Words, not "authz_denied"; both actor and subject on screen.
    expect(within(row).getByText(/Access refused/)).toBeInTheDocument()
    expect(within(row).getByText(/Mr Vance/)).toBeInTheDocument()
    expect(within(row).getByText(/Ada/)).toBeInTheDocument()
  })

  it('says an empty log was read, rather than just showing nothing', async () => {
    overrideApi('/api/admin/security-events', () => payload({ events: [] }))
    render(<AdminSecurityEvents />)

    expect(await screen.findByText(/read and holds no events/)).toBeInTheDocument()
  })

  it('never renders a failed read as an empty log', async () => {
    // An empty log is what someone covering their tracks would want shown.
    overrideApi('/api/admin/security-events',
      () => ({ retrieved: false, events: [], kinds: KINDS }))
    render(<AdminSecurityEvents />)

    expect(await screen.findByText(/could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/holds no events/)).not.toBeInTheDocument()
  })
})

describe('filtering', () => {
  it('asks the backend for one kind rather than filtering in the browser', async () => {
    // Backend `limit` is per query; a function matcher since the router matches the query string exactly.
    const seen = []
    overrideApi(p => p.startsWith('/api/admin/security-events'), (path) => {
      seen.push(path)
      return payload()
    })
    render(<AdminSecurityEvents />)
    await screen.findByRole('listitem')

    await userEvent.click(screen.getByRole('button', { name: 'Rate limited' }))

    await waitFor(() =>
      expect(seen.some(p => p.includes('kind=rate_limited'))).toBe(true))
  })

  it('starts on every kind, because the question is "what happened"', async () => {
    render(<AdminSecurityEvents />)
    const all = await screen.findByRole('button', { name: 'All' })
    expect(all).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('a kind this page does not know', () => {
  it('renders visibly instead of being dropped', async () => {
    // As AlertFeed: a visible row is what gets it reported.
    overrideApi('/api/admin/security-events', () => payload({
      events: [event({ id: 9, kind: 'something_new', subject: null })],
      kinds: [...KINDS, 'something_new'],
    }))
    render(<AdminSecurityEvents />)

    // Scoped to the row: 'Unrecognised' is also the chip label for that kind.
    const row = await screen.findByRole('listitem')
    expect(within(row).getByText(/Unrecognised/)).toBeInTheDocument()
  })
})

describe('an account this page cannot name', () => {
  it('says the profile is missing rather than inventing a name', async () => {
    // `name: null`, not the shared helper's "Student" placeholder.
    overrideApi('/api/admin/security-events', () => payload({
      events: [event({ actor: { id: 'teacher-1', name: null } })],
    }))
    render(<AdminSecurityEvents />)

    const row = await screen.findByRole('listitem')
    expect(within(row).getByText(/An unnamed account/)).toBeInTheDocument()
    expect(within(row).getByText('teacher-1')).toBeInTheDocument()
    expect(within(row).queryByText(/Student/)).not.toBeInTheDocument()
  })

  it('keeps a failed name lookup apart from an account with no profile', async () => {
    // "Could not look up" and "nothing to look up" are two facts.
    overrideApi('/api/admin/security-events', () => payload({
      names_retrieved: false,
      events: [event({ actor: { id: 'teacher-1', name: null } })],
    }))
    render(<AdminSecurityEvents />)

    const row = await screen.findByRole('listitem')
    expect(within(row).getByText(/name could not be read/)).toBeInTheDocument()
    expect(within(row).queryByText(/unnamed account/)).not.toBeInTheDocument()
    // Names failing is not the log failing.
    expect(screen.queryByText(/The security log could not be read/)).not.toBeInTheDocument()
  })

  it('does not leave a possessive dangling in the sentence', async () => {
    overrideApi('/api/admin/security-events', () => payload({
      events: [event({ subject: { id: 'student-1', name: null } })],
    }))
    render(<AdminSecurityEvents />)

    const row = await screen.findByRole('listitem')
    expect(within(row).getByText(/an unnamed account's data/)).toBeInTheDocument()
  })
})
