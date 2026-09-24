/** The live headband badge states on/off, the reading's age, and weak signal. */
import { it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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
  // Two minutes old, past the backend's 90s live window.
  const ts = new Date(Date.now() - 120_000).toISOString()
  renderLive([student({ latest_cognitive: { ts, focus: 0.6, engagement: 0.5, stress: 0.3 } })])
  const badge = await screen.findByText(/Headband on/)
  expect(badge.textContent).toMatch(/stale, 2m ago/)
})

it('says weak signal for a row the mapper nulled for poor contact', async () => {
  // `map_eeg_to_cognitive` keeps the row and nulls the measurements on `contact_poor`.
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
// Roster state is scoped to the selected class; "Nobody's joined yet" means a loaded empty class.

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
  // Loading: neither Sam nor the empty state.
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
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
  await screen.findByRole('combobox')
  expect(screen.queryByText(/Couldn't load/)).toBeNull()
})

it('says the roster could not be loaded instead of leaving the skeleton up', async () => {
  // Once a fetch for the selected class answers either way, the answer wins over the skeleton.
  let calls = 0
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }],
    '/api/teacher/classes/c1/live': () => {
      calls += 1
      if (calls === 1) { const e = new Error('Internal Server Error'); e.status = 500; throw e }
      return [student()]
    },
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  await screen.findByText(/Couldn't load the live roster/)
  expect(screen.queryByText(/Nobody's joined yet/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
  await screen.findByText('Sam')
  expect(screen.queryByText(/Couldn't load/)).toBeNull()
})

it('does not carry one class\'s failure banner over another class\'s loading', async () => {
  // `error` must be scoped to the selected class too.
  let resolveB
  mockApi({
    '/api/classes': () => [{ id: 'c1', name: 'Year 4' }, { id: 'c2', name: 'Year 5' }],
    '/api/teacher/classes/c1/live': () => { const e = new Error('Internal Server Error'); e.status = 500; throw e },
    '/api/teacher/classes/c2/live': () => new Promise(r => { resolveB = r }),
  })
  render(<MemoryRouter><Live /></MemoryRouter>)
  await screen.findByText(/Couldn't load the live roster/)

  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'c2' } })
  await waitFor(() => expect(screen.queryByText(/Couldn't load/)).toBeNull())
  expect(screen.queryByText(/Internal Server Error/)).toBeNull()
  resolveB([student()])
  await screen.findByText('Sam')
  expect(screen.queryByText(/Internal Server Error/)).toBeNull()
})

it('does not read a heuristic "poor" as bad electrodes', () => {
  // The heuristic says poor for any focused student; only a contact verdict or nulled row counts.
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

it('does not draw engagement beside focus', async () => {
  // One number (signal_mapping.py). The sparkline has no sr table, so source is the only checkable surface.
  const ts = new Date().toISOString()
  renderLive([student({ latest_cognitive: { ts, focus: 0.6, engagement: 0.6, stress: 0.3 } })])
  await screen.findByText(/Headband on/)
  expect(screen.queryByText(/engagement/i)).not.toBeInTheDocument()
  // process.cwd(): under jsdom the module URL is not a file: URL.
  const src = readFileSync(join(process.cwd(), 'src/pages/teacher/Live.jsx'), 'utf8')
  expect(src).not.toMatch(/dataKey="engagement"/)
  expect(src).not.toMatch(/key: 'engagement'/)
  expect(src).not.toMatch(/label="Engagement"/)
})
