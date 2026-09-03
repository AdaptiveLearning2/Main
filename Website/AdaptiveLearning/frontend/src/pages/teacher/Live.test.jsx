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
import { render, screen } from '@testing-library/react'
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
