/**
 * The headband badge on a live card says three things, not one.
 *
 * It was a binary "Headband on/off", derived from whether a cognitive row
 * existed this poll -- so a student whose headband dropped two minutes ago
 * and one who never connected rendered the same, and a row the mapper had
 * nulled for poor electrode contact rendered as a healthy "on". The heart
 * badge already showed "weak signal" for an untrusted reading; this brings
 * the headband badge level with it, and adds the age of the reading.
 */
import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { mockApi, resetApi } from '../../test/mocks/apiFetch'
import { eegWeak, formatAge } from '../../lib/signalAge'
import Live from './Live'

const CLASSES = [{ id: 'c1', name: 'Year 4' }]

function student(overrides) {
  return {
    user_id: 's1', name: 'Sam', email: 's@x.y',
    active_session: { id: 'sess-1' },
    latest_cognitive: null, latest_face: null, latest_heart: null,
    ...overrides,
  }
}

function renderLive(rows) {
  mockApi({
    '/api/classes': () => CLASSES,
    '/api/teacher/classes/c1/live': () => rows,
  })
  return render(<MemoryRouter><Live /></MemoryRouter>)
}

beforeEach(() => resetApi())

it('shows how old the newest headband reading is', async () => {
  const ts = new Date(Date.now() - 12_000).toISOString()
  renderLive([student({ latest_cognitive: { ts, focus: 0.6, engagement: 0.5, stress: 0.3 } })])
  const badge = await screen.findByText(/Headband on/)
  expect(badge.textContent).toMatch(/1[0-9]s ago/)
  expect(badge.textContent).not.toMatch(/stale|weak/)
})

it('marks a reading past the live window as stale rather than dropping it', async () => {
  // Two minutes old: the session is still open, the row still exists, and a
  // binary badge would have said "on". The backend's own live window is 90s.
  const ts = new Date(Date.now() - 120_000).toISOString()
  renderLive([student({ latest_cognitive: { ts, focus: 0.6, engagement: 0.5, stress: 0.3 } })])
  const badge = await screen.findByText(/Headband on/)
  expect(badge.textContent).toMatch(/stale, 2m ago/)
})

it('says weak signal for a row the mapper nulled for poor contact', async () => {
  // `map_eeg_to_cognitive` keeps the row and nulls the measurements on
  // `contact_poor`, so this is what a badly-seated headband looks like from
  // here: on, recent, and empty.
  const ts = new Date().toISOString()
  renderLive([student({ latest_cognitive: {
    ts, focus: null, engagement: null, stress: null,
    raw: { signal_quality: 'poor', quality_basis: 'contact' },
  } })])
  const badge = await screen.findByText(/Headband on/)
  expect(badge.textContent).toMatch(/weak signal/)
})

it('keeps a student with no row at all as plain off', async () => {
  renderLive([student()])
  const badge = await screen.findByText(/Headband off/)
  expect(badge.textContent).not.toMatch(/ago|weak|stale/)
})

// ── switching class ──────────────────────────────────────────────────────────
//
// `students` used to be whatever the last fetch returned, whichever class it
// was for, so the previous class's cards stayed on screen under the new
// class's name until the new response landed -- and before any response, the
// page read "Nobody's joined yet", which is the wording for a *loaded* empty
// class. Seen by hand: selecting an empty class showed the other class's
// student for several seconds.

it('does not show the previous class under the new class name while it loads', async () => {
  let resolveB
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }, { id: 'c2', name: 'Year 5' }],
    '/api/teacher/classes/c1/live': () => [student()],
    '/api/teacher/classes/c2/live': () => new Promise(r => { resolveB = r }),
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  await screen.findByText('Sam')

  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'c2' } })
  // Loading, not Sam, and not the empty state either -- nothing has been
  // fetched for Year 5 yet, so nothing can be claimed about it.
  await waitFor(() => expect(screen.queryByText('Sam')).toBeNull())
  expect(screen.queryByText(/Nobody's joined yet/)).toBeNull()

  resolveB([])
  await screen.findByText(/Nobody's joined yet/)
})

it('shows the empty state only once a fetch for the selected class has answered', async () => {
  let resolveA
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': () => new Promise(r => { resolveA = r }),
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  await waitFor(() => expect(resolveA).toBeDefined())
  expect(screen.queryByText(/Nobody's joined yet/)).toBeNull()
  resolveA([])
  await screen.findByText(/Nobody's joined yet/)
})

it('says the class list could not be loaded, not that there are no classes', async () => {
  // A failed read left `classes` at [] and the page said "No classes yet --
  // create a class first", pointing a teacher whose classes exist at the one
  // action that could not help. Same failure class as the roster above.
  let calls = 0
  mockApi({
    '/api/classes': () => {
      calls += 1
      if (calls === 1) { const e = new Error('boom'); e.status = 500; throw e }
      return [{ id: 'c1', name: 'Year 4' }]
    },
    '/api/teacher/classes/c1/live': () => [],
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  await screen.findByText(/Couldn't load your classes/)
  expect(screen.queryByText(/No classes yet/)).toBeNull()
  // Retry is offered for a failure that can pass, and works.
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
  await screen.findByRole('combobox')
  expect(screen.queryByText(/Couldn't load/)).toBeNull()
})

it('does not read a heuristic "poor" as bad electrodes', () => {
  // The legacy heuristic reports poor for any focused student; only a
  // contact-backed verdict, or the nulled row it produces, counts.
  expect(eegWeak({ focus: 0.7, engagement: 0.6, stress: 0.2,
                   raw: { signal_quality: 'poor', quality_basis: 'heuristic' } })).toBe(false)
  expect(eegWeak({ focus: null, engagement: null, stress: null })).toBe(true)
  expect(eegWeak(null)).toBe(false)
})

it('formats ages in seconds under a minute and minutes after', () => {
  expect(formatAge(0)).toBe('0s ago')
  expect(formatAge(59_400)).toBe('59s ago')
  expect(formatAge(60_000)).toBe('1m ago')
  expect(formatAge(185_000)).toBe('3m ago')
  expect(formatAge(null)).toBeNull()
})
