import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import ParentDashboard from './Dashboard'

// This page shows a Face Attention tile per child, sourced from the batch
// summary RPC. It is the page a parent lands on, so a facial-recognition
// opt-out honoured on the child's report but ignored here would put the data
// back on screen the moment they hit back -- a guarantee the app would not be
// keeping.

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

// What the backend returns with the opt-out on: the aggregate never read a
// facial row, so the average is null and the sample count zero -- which on
// their own are indistinguishable from a child the camera never saw.
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
  // Was asserted through the Face Attention tile, which is gone -- `attention`
  // has no producer. The behaviour it guarded is still live: a payload built
  // before `emotion_included` existed must not be reported as a channel the
  // parent switched off, so the note below the tiles keeps its "not read"
  // wording for genuine exclusions only.
  apiFetch.mockImplementation(() => {
    const { face_included, ...summary } = withFace[0].signal_summary  // eslint-disable-line no-unused-vars
    return Promise.resolve([{ ...withFace[0], signal_summary: summary }])
  })
  renderDashboard()
  await screen.findByText('Ada')
  expect(screen.queryByText(/facial signals were not read/i)).not.toBeInTheDocument()
})

it('does not show a row of N/As for a reading it has no tile for', async () => {
  // hasSignalSummary tracks what the tiles can render, not what the summary
  // carries. engagement is in the payload but has no tile on this page, so a
  // child whose only reading is engagement has nothing to show here -- and
  // admitting it would produce four N/As, the "something is broken" display
  // the check exists to avoid.
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
  // hasSignalSummary reaches "no data" without consulting a single facial
  // reading, so the copy has to be clear that is the scope of the claim --
  // otherwise a parent reads it as covering everything.
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
  // The absence it must not report is the one it never measured.
  expect(screen.queryByText(/facial-recognition signal data yet/i)).not.toBeInTheDocument()
})

it('does not tell a parent their child recorded nothing when the read failed', async () => {
  // The endpoint swallows a failed aggregate so one broken RPC does not blank
  // the dashboard, and answers 200 with an all-default summary. Every check on
  // this page read that as a quiet week, so a broken read reached a parent as
  // "no weekly EEG or facial-recognition signal data yet" -- an absence
  // asserted from data that never loaded.
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
  // The academic figures come from user_stats and are unaffected by it.
  expect(tile('Questions').getByText('10')).toBeInTheDocument()
  expect(tile('Accuracy').getByText('60%')).toBeInTheDocument()
})

it('still reports a genuine quiet week when the read succeeded', async () => {
  // The mirror of the above, so the new flag cannot be satisfied by treating
  // every empty summary as a failure.
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
  // With nothing on screen there is nothing to preserve, and a bare banner over
  // an empty page would read as "no children linked".
  apiFetch.mockImplementation(() => Promise.reject(new Error('backend down')))
  renderDashboard()

  await screen.findByText(/make sure the backend is running/i)
  expect(screen.queryByText(/couldn't refresh this page just now/i)).not.toBeInTheDocument()
})

it('asks for the children without a viewer-side flag', async () => {
  // The parent's control over what is recorded now lives on the Settings page
  // and is stored consent, not a per-browser switch that narrowed the query.
  renderDashboard()

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const urls = apiFetch.mock.calls.map(c => String(c[0]))
  expect(urls.some(u => u.includes('/api/parent/children'))).toBe(true)
  expect(urls.join(' ')).not.toMatch(/include_face/)
})
