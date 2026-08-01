import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

function urls() {
  return apiFetch.mock.calls.map(c => String(c[0]))
}

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

it('includes facial data by default', async () => {
  renderDashboard()
  await screen.findByText('Ada')
  expect(urls()[0]).toContain('include_face=true')
  expect(tile('Face Attention').getByText('85%')).toBeInTheDocument()
})

it('re-fetches the children with the flag when the switch flips', async () => {
  // The tile is served by the summary RPC, so honouring the switch means
  // asking the backend again -- there is nothing to hide client-side.
  renderDashboard()
  await screen.findByText('Ada')

  await userEvent.click(screen.getByRole('switch'))

  await waitFor(() => expect(urls()).toHaveLength(2))
  expect(urls()[1]).toContain('include_face=false')
})

it('labels the tile as off rather than missing', async () => {
  // "N/A" would report a measurement the camera failed to take, rather than a
  // choice the viewer made.
  renderDashboard()
  await screen.findByText('Ada')
  await userEvent.click(screen.getByRole('switch'))
  await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
})

it('leaves the EEG tiles untouched', async () => {
  renderDashboard()
  await screen.findByText('Ada')
  await userEvent.click(screen.getByRole('switch'))
  await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
  expect(tile('Weekly Focus').getByText('72%')).toBeInTheDocument()
  expect(tile('Weekly Stress').getByText('31%')).toBeInTheDocument()
})

it('shares the choice with the rest of the app', async () => {
  // One stored preference, not one per page: a parent who switched facial
  // reporting off on a child's report must not find it back on here.
  localStorage.setItem('signal_include_face', 'false')
  renderDashboard()
  await screen.findByText('Ada')
  expect(urls()[0]).toContain('include_face=false')
})

it('does not claim facial data is absent when it was never requested', async () => {
  apiFetch.mockImplementation((url) => Promise.resolve([{
    ...withoutFace[0],
    signal_summary: {
      focus: null, stress: null, face_attention: null, sessions: 0,
      cognitive_samples: 0, face_samples: 0,
      face_included: !String(url).includes('include_face=false'),
    },
  }]))
  renderDashboard()
  await screen.findByText('Ada')
  expect(screen.getByText(/no weekly EEG or facial-recognition signal data yet/i)).toBeInTheDocument()

  await userEvent.click(screen.getByRole('switch'))
  // Naming facial recognition here would report an absence never measured.
  await waitFor(() => expect(screen.getByText(/no weekly EEG signal data yet/i)).toBeInTheDocument())
  expect(screen.queryByText(/facial-recognition signal data yet/i)).not.toBeInTheDocument()
})

it('still renders facial data for payloads predating the flag', async () => {
  apiFetch.mockImplementation(() => {
    const { face_included, ...summary } = withFace[0].signal_summary  // eslint-disable-line no-unused-vars
    return Promise.resolve([{ ...withFace[0], signal_summary: summary }])
  })
  renderDashboard()
  await screen.findByText('Ada')
  expect(tile('Face Attention').getByText('85%')).toBeInTheDocument()
})
