import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import AdminGuard from './AdminGuard'

// Not the security boundary -- every /api/admin/* endpoint checks again --
// but it decides what the browser renders while the backend check is in
// flight.

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
  // The role on the session comes from user_metadata, which the client can
  // rewrite -- so this must ask the backend instead of trusting it.
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

  // An unanswered check is not a refusal -- treating it as one would bounce
  // an admin off their own page on every load.
  expect(screen.getByText('loading')).toBeInTheDocument()
  expect(screen.queryByText('console')).not.toBeInTheDocument()
  expect(screen.queryByText(/redirected/)).not.toBeInTheDocument()

  resolve({ is_admin: true })
  await screen.findByText('console')
})

it('redirects a non-admin away instead of showing an error', async () => {
  // They followed a stale link, not a broken feature, so redirect rather than
  // show an error.
  apiFetch.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('redirected to /dashboard')
  expect(screen.queryByText('console')).not.toBeInTheDocument()
})

it('does not admit anyone when the check itself fails', async () => {
  // Fails closed -- a network blip must not be a way in.
  apiFetch.mockRejectedValue(new Error('network'))

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('redirected to /dashboard')
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
