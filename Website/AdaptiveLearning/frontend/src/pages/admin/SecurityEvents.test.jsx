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
  retrieved: true, kinds: KINDS, events: [event()], ...over,
})

beforeEach(() => {
  resetApi()
  mockApi({ '/api/admin/security-events': () => payload() })
})

describe('the three states it has to keep apart', () => {
  it('shows what happened, in words rather than column names', async () => {
    render(<AdminSecurityEvents />)

    // Scoped to the row, because "Access refused" is also a filter chip and a
    // bare findByText matches both -- and a multiple-match error is *retried*
    // by findBy until the 5 s budget, so it surfaces as a timeout rather than
    // as the ambiguity it is. Same trap CLAUDE.md records for `getByText('Focus')`.
    const row = await screen.findByRole('listitem')

    // Not "authz_denied": accurate, and it tells an admin nothing they can act
    // on. The actor and the subject both have to be on screen -- "who tried to
    // read this child's record" is the question this page exists to answer.
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
    // The worst available wrong answer on this surface: an empty security log
    // is exactly what someone covering their tracks would want it to look
    // like, so "could not be read" and "is empty" must never share a rendering.
    overrideApi('/api/admin/security-events',
      () => ({ retrieved: false, events: [], kinds: KINDS }))
    render(<AdminSecurityEvents />)

    expect(await screen.findByText(/could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/holds no events/)).not.toBeInTheDocument()
  })
})

describe('filtering', () => {
  it('asks the backend for one kind rather than filtering in the browser', async () => {
    // Filtering client-side would mean the page holds every kind's rows and
    // shows a subset -- and the `limit` the backend clamps is per *query*, so
    // a browser-side filter would silently show fewer than it claims.
    // A function matcher, not the bare path: the filtered request is
    // `...?kind=rate_limited`, and the router matches paths exactly, so a
    // string route would leave the second call *unrouted* -- which throws by
    // design, and surfaces here as a timeout rather than as the missing route
    // it is.
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
    // Same rule as AlertFeed: the CHECK constraint makes this near impossible,
    // and if it happens a visible row is what gets it reported. Dropping it
    // would make a newer backend's events invisible here with nothing saying so.
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
