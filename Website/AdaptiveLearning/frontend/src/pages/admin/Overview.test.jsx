import { render, screen, cleanup, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import AdminOverview from './Overview'
import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

// Search results are keyed to their query, so a stale response cannot repaint under a newer term.

const HEALTH = {
  retrieved: true,
  checks: [{ key: 'eeg_sidecar', status: 'ok', detail: 'up' }],
}
const CONSENT = {
  retrieved: true, students: 3, eeg: 2, headband_optical: 1, camera: 0,
  awaiting_student_ack: 0,
}

const ADA = { id: 'u-1', display_name: 'Ada Lovelace', email: 'ada@example.com' }
const GRACE = { id: 'u-2', display_name: 'Grace Hopper', email: 'grace@example.com' }

const searchPath = q => `/api/admin/students/search?q=${encodeURIComponent(q)}`

beforeEach(() => {
  resetApi()
  // `shouldAdvanceTime` keeps Testing Library polling and userEvent delays moving.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockApi({
    '/api/admin/health': HEALTH,
    '/api/admin/consent-summary': CONSENT,
    [searchPath('ada')]: { students: [ADA] },
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

// Tell userEvent which clock is running, or keystrokes hang against the fake one.
const setup = () => {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  render(<MemoryRouter><AdminOverview /></MemoryRouter>)
  return user
}

const box = () => screen.getByPlaceholderText(/Find a student/)
const searchCalls = () =>
  apiFetch.mock.calls.map(c => String(c[0])).filter(p => p.includes('/students/search'))

// Lets the debounce fire and the promise it started settle.
const settle = async () => {
  await act(async () => { vi.advanceTimersByTime(300) })
}

describe('the student search', () => {
  it('does not query for a single character', async () => {
    const user = setup()
    await user.type(box(), 'a')
    await settle()
    expect(searchCalls()).toEqual([])
  })

  it('queries once the term is long enough, and lists what comes back', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(searchCalls()).toEqual([searchPath('ada')])
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
  })

  it('says it is searching until the results catch up with the box', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    expect(screen.getByText('Searching…')).toBeInTheDocument()
    await settle()
    expect(screen.queryByText('Searching…')).not.toBeInTheDocument()
  })

  it('debounces, rather than querying per keystroke', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(searchCalls()).toHaveLength(1)
  })

  it('keeps the last results on screen while a newer query is in flight', async () => {
    // Stale hits stay, disclosed as stale, rather than blanking on every keystroke.
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()

    // One more character: a new query is now pending and the old hits are stale.
    await user.type(box(), 'm')
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText(/Searching/)).toBeInTheDocument()
  })

  it('names the term the visible results actually answer', async () => {
    // Safe only if it is clear they are not for what is in the box.
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    await user.type(box(), 'm')

    expect(screen.getByText(/showing results for/)).toHaveTextContent('ada')
  })

  it('takes the stale results out of reach until the new answer lands', async () => {
    // Stale rows belong to the previous query, so they must not be clickable.
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    const link = screen.getByText('Ada Lovelace').closest('a')
    expect(link).not.toHaveAttribute('aria-disabled')

    await user.type(box(), 'm')

    // Still readable, deliberately -- but not clickable and not tabbable.
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(link).toHaveAttribute('aria-disabled', 'true')
    expect(link).toHaveAttribute('tabindex', '-1')
    expect(link.closest('ul')).toHaveClass('pointer-events-none')
    expect(link.closest('ul')).toHaveAttribute('aria-busy', 'true')
  })

  it('drops the list when the term falls back below two characters', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()

    await user.clear(box())
    await user.type(box(), 'a')
    await settle()
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
    expect(screen.queryByText('Searching…')).not.toBeInTheDocument()
  })

  it('ignores a response that arrives after the term has moved on', async () => {
    // The first query is slow, so its response lands after a later one.
    let releaseAda
    overrideApi(searchPath('ada'), () => new Promise(r => { releaseAda = r }))
    overrideApi(searchPath('adam'), { students: [GRACE] })

    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    await user.type(box(), 'm')
    await settle()
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()

    await act(async () => { releaseAda({ students: [ADA] }) })
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
    expect(screen.getByText('Grace Hopper')).toBeInTheDocument()
  })

  it('shows no students when the search fails, rather than the last query hits', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()

    overrideApi(searchPath('adam'), () => Promise.reject(apiError(500)))
    await user.type(box(), 'm')
    await settle()
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
    expect(screen.queryByText('Searching…')).not.toBeInTheDocument()
  })

  it('links a result to that student\'s report', async () => {
    const user = setup()
    await user.type(box(), 'ada')
    await settle()
    expect(screen.getByRole('link', { name: /Ada Lovelace/ }))
      .toHaveAttribute('href', '/teacher/students/u-1/report')
  })
})

describe('the surrounding panels', () => {
  // A failed read must not render as "nothing to report".
  it('says the consent counts could not be read when the read failed', async () => {
    overrideApi('/api/admin/consent-summary', { retrieved: false })
    setup()
    expect(await screen.findByText(/Could not read consent counts/)).toBeInTheDocument()
  })

  it('renders the consent counts as counts only', async () => {
    setup()
    expect(await screen.findByText('Students')).toBeInTheDocument()
    expect(screen.getByText(/Which student agreed to what is not shown/)).toBeInTheDocument()
  })
})
