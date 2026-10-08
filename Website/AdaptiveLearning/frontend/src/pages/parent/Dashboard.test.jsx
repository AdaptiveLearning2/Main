import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import ParentDashboard from './Dashboard'
import { pages } from '../../routes'

// The facial opt-out must hold here as on the child's report.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'parent@example.com' } }),
}))
// Records what the page warms; a real chunk load mid-test would outlive the test.
const mockPrefetched = []
vi.mock('../../lib/prefetch', () => ({ usePrefetchWhenIdle: loader => { mockPrefetched.push(loader) } }))

const { apiFetch } = await import('../../lib/api')

const withFace = [{
  user_id: 'kid-1',
  name: 'Ada',
  email: 'ada@example.com',
  stats: { total_questions: 10, total_correct: 6, current_streak: 2 },
  sessions: [],
  signal_summary: {
    focus: 0.72, stress: 0.31, face_attention: 0.85, sessions: 3,
    cognitive_samples: 100, face_samples: 40, face_included: true,
  },
}]

// What the backend returns with the opt-out on: null average, zero samples.
const withoutFace = [{
  ...withFace[0],
  signal_summary: {
    ...withFace[0].signal_summary,
    face_attention: null, face_samples: 0, face_included: false,
  },
}]

function tile(label) {
  return within(screen.getByText(label).closest('div'))
}

beforeEach(() => {
  localStorage.clear()
  apiFetch.mockReset()
  apiFetch.mockImplementation((url) =>
    Promise.resolve(String(url).includes('include_face=false') ? withoutFace : withFace))
})

function renderDashboard() {
  return render(<MemoryRouter><ParentDashboard /></MemoryRouter>)
}

it("warms a child's report, the usual next click, once the page has rendered", () => {
  apiFetch.mockResolvedValue([])
  renderDashboard()
  expect(mockPrefetched).toContain(pages.parentChild)
})








it('does not read a pre-flag payload as facial data being withheld', async () => {
  // A pre-`emotion_included` payload is not a channel switched off.
  apiFetch.mockImplementation(() => {
    const { face_included, ...summary } = withFace[0].signal_summary
    return Promise.resolve([{ ...withFace[0], signal_summary: summary }])
  })
  renderDashboard()
  await screen.findByText('Ada')
  expect(screen.queryByText(/facial signals were not read/i)).not.toBeInTheDocument()
})

it('does not show a row of N/As for a reading it has no tile for', async () => {
  // engagement has no tile here, so it alone must not produce N/As.
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    signal_summary: {
      focus: null, stress: null, engagement: 0.64, face_attention: null,
      sessions: 0, cognitive_samples: 12, face_samples: 0, face_included: true,
    },
  }]))

  renderDashboard()

  await screen.findByText('Ada')
  await screen.findByText(/no weekly EEG or facial-recognition signal data yet/i)
  expect(screen.queryByText('Weekly Focus')).not.toBeInTheDocument()
})

it('says facial signals were not read when there is nothing else to show', async () => {
  const empty = [{
    ...withFace[0],
    signal_summary: {
      focus: null, stress: null, face_attention: null, sessions: 0,
      cognitive_samples: 0, face_samples: 0, face_included: false,
    },
  }]
  apiFetch.mockImplementation(() => Promise.resolve(empty))

  renderDashboard()

  await screen.findByText(/no weekly EEG signal data yet, and facial signals were not read/i)
  expect(screen.queryByText(/facial-recognition signal data yet/i)).not.toBeInTheDocument()
})

it('does not tell a parent their child recorded nothing when the read failed', async () => {
  // A failed aggregate answers 200 with defaults; not a quiet week.
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    signal_summary: {
      focus: null, stress: null, engagement: null, face_attention: null,
      sessions: 0, cognitive_samples: 0, face_samples: 0,
      face_included: true, retrieved: false,
    },
  }]))

  renderDashboard()

  await screen.findByText(/signal data couldn't be loaded/i)
  expect(screen.queryByText(/no weekly EEG or facial-recognition signal data yet/i)).not.toBeInTheDocument()
  // Academic figures come from user_stats and are unaffected.
  expect(tile('Questions').getByText('10')).toBeInTheDocument()
  expect(tile('Accuracy').getByText('60%')).toBeInTheDocument()
})

it('still reports a genuine quiet week when the read succeeded', async () => {
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    signal_summary: {
      focus: null, stress: null, engagement: null, face_attention: null,
      sessions: 0, cognitive_samples: 0, face_samples: 0,
      face_included: true, retrieved: true,
    },
  }]))

  renderDashboard()

  await screen.findByText(/no weekly EEG or facial-recognition signal data yet/i)
  expect(screen.queryByText(/couldn't be loaded/i)).not.toBeInTheDocument()
})


it('still takes over the page when the very first load fails', async () => {
  // Nothing on screen to preserve, so a full-page error is appropriate here.
  apiFetch.mockImplementation(() => Promise.reject(new Error('backend down')))
  renderDashboard()

  await screen.findByText(/make sure the backend is running/i)
  expect(screen.queryByText(/couldn't refresh this page just now/i)).not.toBeInTheDocument()
})

const statTile = (label) => screen.getByText(label, { selector: 'p' }).previousElementSibling

it('shows an unread stats block as unread, not as a child with no work', async () => {
  // A failed user_stats read arrives as zeros with `retrieved: false`.
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    stats: { total_questions: 0, total_correct: 0, current_streak: 0, retrieved: false },
  }]))
  renderDashboard()

  expect(await screen.findByText('These practice totals could not be loaded.')).toBeInTheDocument()
  for (const label of ['Questions', 'Accuracy', 'Streak', 'Correct']) {
    expect(statTile(label)).toHaveTextContent('—')
  }
})

it('shows real totals when the stats read succeeded', async () => {
  renderDashboard()
  await screen.findByText('Ada')
  expect(statTile('Accuracy')).toHaveTextContent('60%')
  expect(statTile('Questions')).toHaveTextContent('10')
  expect(screen.queryByText('These practice totals could not be loaded.')).not.toBeInTheDocument()
})

it('says why a weekly focus figure is missing, not a raw N/A', async () => {
  // EEG withdrawn last week; sessions still happened this week, so the tiles render.
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    signal_summary: { ...withFace[0].signal_summary, focus: null, stress: null,
                      cognitive_samples: 0, eeg_enabled: false,
                      eeg_revoked_at: '2026-09-03T09:00:00Z', consent_retrieved: true },
  }]))
  renderDashboard()

  expect(await screen.findByText('Weekly Focus')).toBeInTheDocument()
  expect(statTile('Weekly Focus')).toHaveTextContent(/^Off since/)
  expect(statTile('Weekly Stress')).toHaveTextContent(/^Off since/)
  expect(screen.queryByText('N/A')).not.toBeInTheDocument()
})

it('says the weekly focus was erased, with EEG still on, rather than no sensor', async () => {
  apiFetch.mockImplementation(() => Promise.resolve([{
    ...withFace[0],
    signal_summary: { ...withFace[0].signal_summary, focus: null, stress: null,
                      cognitive_samples: 0, eeg_enabled: true, consent_retrieved: true,
                      eeg_erased_at: '2026-10-07T00:40:00Z' },
  }]))
  renderDashboard()

  expect(await screen.findByText('Weekly Focus')).toBeInTheDocument()
  expect(statTile('Weekly Focus')).toHaveTextContent(/^Erased /)
})

it('asks for the children without a viewer-side flag', async () => {
  // Consent on Settings controls recording, not a switch on this query.
  renderDashboard()

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const urls = apiFetch.mock.calls.map(c => String(c[0]))
  expect(urls.some(u => u.includes('/api/parent/children'))).toBe(true)
  expect(urls.join(' ')).not.toMatch(/include_face/)
})
