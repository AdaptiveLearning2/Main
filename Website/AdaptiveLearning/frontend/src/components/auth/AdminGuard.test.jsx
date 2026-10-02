import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import AdminGuard from './AdminGuard'

// Not the security boundary (every /api/admin/* endpoint re-checks); it decides what renders meanwhile.

const apiFetch = vi.fn()
let authState

vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))
vi.mock('../../context/AuthContext', () => ({ useAuth: () => authState }))
vi.mock('../ui/PageLoader', () => ({ default: () => <div>loading</div> }))
vi.mock('react-router-dom', () => ({
  Navigate: ({ to }) => <div>redirected to {to}</div>,
}))

beforeEach(() => {
  apiFetch.mockReset()
  authState = { user: { id: 'u1' }, loading: false }
})

it('asks the backend rather than reading a role from the session', async () => {
  // The session's role is client-writable user_metadata.
  authState = { user: { id: 'u1' }, loading: false, role: 'student' }
  apiFetch.mockResolvedValue({ is_admin: true })

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('console')
  expect(apiFetch).toHaveBeenCalledWith('/api/admin/me')
})

it('renders nothing but a loader while the check is in flight', async () => {
  let resolve
  apiFetch.mockReturnValue(new Promise(r => { resolve = r }))

  render(<AdminGuard><div>console</div></AdminGuard>)

  // An unanswered check is not a refusal.
  expect(screen.getByText('loading')).toBeInTheDocument()
  expect(screen.queryByText('console')).not.toBeInTheDocument()
  expect(screen.queryByText(/redirected/)).not.toBeInTheDocument()

  resolve({ is_admin: true })
  await screen.findByText('console')
})

it('redirects a non-admin away instead of showing an error', async () => {
  // A stale link, not a broken feature: redirect.
  apiFetch.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('redirected to /dashboard')
  expect(screen.queryByText('console')).not.toBeInTheDocument()
})

it('does not admit anyone when the check itself fails, and says it failed', async () => {
  // Fails closed -- a blip must not be a way in, and must not read as "you are not an admin".
  apiFetch.mockRejectedValue(Object.assign(new Error('Could not check admin access'), { status: 503 }))

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText("Couldn't load the admin console just now. Try again in a moment.")
  expect(screen.queryByText('console')).not.toBeInTheDocument()
  expect(screen.queryByText(/redirected/)).not.toBeInTheDocument()
})

it('does not check again when a token refresh replaces the user object', async () => {
  // Same account, new object: a re-check that failed would replace an open console with an error.
  apiFetch.mockResolvedValue({ is_admin: true })
  const { rerender } = render(<AdminGuard><div>console</div></AdminGuard>)
  await screen.findByText('console')

  authState = { user: { id: 'u1', refreshed: true }, loading: false }
  rerender(<AdminGuard><div>console</div></AdminGuard>)

  expect(screen.getByText('console')).toBeInTheDocument()
  expect(apiFetch).toHaveBeenCalledTimes(1)
})

it('checks again on Try again, and admits an admin once the check answers', async () => {
  apiFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))
  apiFetch.mockResolvedValueOnce({ is_admin: true })

  render(<AdminGuard><div>console</div></AdminGuard>)

  fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))
  await screen.findByText('console')
  expect(apiFetch).toHaveBeenCalledTimes(2)
})

it('sends a signed-out visitor to the login page, not the dashboard', async () => {
  authState = { user: null, loading: false }

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('redirected to /login')
  expect(apiFetch).not.toHaveBeenCalled()
})

it('waits for auth before asking, so the call carries a token', async () => {
  authState = { user: null, loading: true }
  const { rerender } = render(<AdminGuard><div>console</div></AdminGuard>)

  expect(apiFetch).not.toHaveBeenCalled()
  expect(screen.getByText('loading')).toBeInTheDocument()

  apiFetch.mockResolvedValue({ is_admin: true })
  authState = { user: { id: 'u1' }, loading: false }
  rerender(<AdminGuard><div>console</div></AdminGuard>)

  await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1))
})
