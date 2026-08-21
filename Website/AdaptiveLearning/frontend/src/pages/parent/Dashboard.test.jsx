import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import ParentDashboard from './Dashboard'

// A facial-recognition opt-out honoured on the child's report but ignored
// here would put the data back on screen the moment a parent hits back.

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }))
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'parent@example.com' } }),
}))

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








it('does not read a pre-flag payload as facial data being withheld', async () => {
  // A payload built before `emotion_included` existed must not be reported
  // as a channel the parent switched off.
  apiFetch.mockImplementation(() => {
    const { face_included, ...summary } = withFace[0].signal_summary
    return Promise.resolve([{ ...withFace[0], signal_summary: summary }])
  })
  renderDashboard()
  await screen.findByText('Ada')
  expect(screen.queryByText(/facial signals were not read/i)).not.toBeInTheDocument()
})

it('does not show a row of N/As for a reading it has no tile for', async () => {
  // engagement is in the payload but has no tile on this page, so a child
  // whose only reading is engagement should show nothing here, not N/As.
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
  // A failed aggregate still answers 200 with an all-default summary, which
  // must not read as a genuine quiet week.
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

it('asks for the children without a viewer-side flag', async () => {
  // What's recorded is controlled by consent on the Settings page, not a
  // per-browser switch on this query.
  renderDashboard()

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const urls = apiFetch.mock.calls.map(c => String(c[0]))
  expect(urls.some(u => u.includes('/api/parent/children'))).toBe(true)
  expect(urls.join(' ')).not.toMatch(/include_face/)
})
