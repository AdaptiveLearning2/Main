import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import AdminGuard from './AdminGuard'

// The gate in front of the switch that turns consent enforcement off.
//
// It is not the security boundary -- every /api/admin/* endpoint checks again
// -- but it decides what a browser renders, and the state that matters is the
// one in between: while the check is in flight the answer is not yet "no".

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
  // The whole reason this component exists instead of RoleGuard: the role on
  // the session comes from user_metadata, which the client can rewrite.
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

  // Not redirected, and not shown. An unanswered check is not a refusal, and
  // treating it as one bounces an admin off their own page on every load.
  expect(screen.getByText('loading')).toBeInTheDocument()
  expect(screen.queryByText('console')).not.toBeInTheDocument()
  expect(screen.queryByText(/redirected/)).not.toBeInTheDocument()

  resolve({ is_admin: true })
  await screen.findByText('console')
})

it('redirects a non-admin away instead of showing an error', async () => {
  // They did not ask for this page -- they followed a stale link or typed the
  // URL -- so an error would be about a problem they do not have.
  apiFetch.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))

  render(<AdminGuard><div>console</div></AdminGuard>)

  await screen.findByText('redirected to /dashboard')
  expect(screen.queryByText('console')).not.toBeInTheDocument()
})

it('does not admit anyone when the check itself fails', async () => {
  // Fails closed. A network blip must not be a way in.
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
