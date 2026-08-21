import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

// The display preference is stored in localStorage, scoped to the browser
// rather than the account, so every sign-out path must clear it or the next
// person on a shared machine inherits it.

const signOut = vi.fn()
const getSession = vi.fn()
const apiFetch = vi.fn()
let authCallback

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: (...args) => getSession(...args),
      onAuthStateChange: (cb) => {
        authCallback = cb
        return { data: { subscription: { unsubscribe: () => {} } } }
      },
      signOut: (...args) => signOut(...args),
    },
  },
}))

vi.mock('../lib/api', () => ({ apiFetch: (...args) => apiFetch(...args) }))

function SignOutButton() {
  const { signOut: doSignOut } = useAuth()
  return <button onClick={() => doSignOut().catch(() => {})}>Sign out</button>
}

function renderAuth() {
  return render(<AuthProvider><SignOutButton /></AuthProvider>)
}

/** Renders the resolved role, plus whether the provider still says loading. */
function RoleProbe() {
  const { role, loading } = useAuth()
  return <div>{loading ? 'loading' : `role:${role}`}</div>
}

const SESSION = (metadataRole) => ({
  user: { id: 'u1', user_metadata: metadataRole ? { role: metadataRole } : {} },
})

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('teacher_hide_sensor_data', 'true')
  signOut.mockReset()
  signOut.mockResolvedValue({ error: null })
  getSession.mockReset()
  getSession.mockResolvedValue({ data: { session: null } })
  apiFetch.mockReset()
  apiFetch.mockResolvedValue({ role: 'student' })
})

it('clears the teacher display preference on sign-out', async () => {
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('clears it even when sign-out fails', async () => {
  // Must clear even on failure — the user still ends up at the login screen.
  signOut.mockRejectedValue(new Error('offline'))
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('clears it on a sign-out this tab did not perform', async () => {
  // An expired refresh token, or a sign-out in another tab, arrives as an
  // auth state change rather than through signOut() above.
  renderAuth()
  await screen.findByText('Sign out')
  authCallback('SIGNED_OUT', null)
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('leaves it alone while the session is live', async () => {
  renderAuth()
  await screen.findByText('Sign out')
  authCallback('TOKEN_REFRESHED', { user: { id: 'u1', user_metadata: { role: 'parent' } } })
  expect(localStorage.getItem('teacher_hide_sensor_data')).toBe('true')
})


// ── where the role comes from ─────────────────────────────────────────────

it('takes the role from the backend, not from the claim in the session', async () => {
  // An account promoted directly in the database has no role in its
  // session metadata, so the role must come from the backend, not the claim.
  getSession.mockResolvedValue({ data: { session: SESSION(null) } })
  apiFetch.mockResolvedValue({ role: 'admin' })

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:admin')).toBeInTheDocument()
  expect(apiFetch).toHaveBeenCalledWith('/api/profile/me', expect.any(Object))
})

it('believes the backend over a claim that disagrees', async () => {
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockResolvedValue({ role: 'student' })

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:student')).toBeInTheDocument()
})

it('says loading until the role has actually resolved', async () => {
  // The window that matters is after the session resolves but before the
  // profile does — asserting "loading" right after render proves nothing,
  // since `authLoading` is true then regardless.
  let resolve
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockReturnValue(new Promise(r => { resolve = r }))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  // Flush the session promise so only the profile read is left pending.
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  expect(screen.queryByText('role:null')).not.toBeInTheDocument()
  expect(screen.getByText('loading')).toBeInTheDocument()

  resolve({ role: 'teacher' })
  expect(await screen.findByText('role:teacher')).toBeInTheDocument()
})

it('bounds the role read, so a hung request cannot strand the app', async () => {
  // A `.catch` alone isn't a bound — a hung request never rejects, so this
  // asserts the call actually carries a timeout.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockReturnValue(new Promise(() => {}))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const [, opts] = apiFetch.mock.calls[0]
  expect(opts?.timeoutMs).toBeGreaterThan(0)
})

it('falls back to the claim when the backend cannot be reached', async () => {
  // A blip is not a demotion — falling back to 'student' would drop every
  // teacher into the wrong app whenever the API was down.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockRejectedValue(new Error('offline'))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:teacher')).toBeInTheDocument()
})

it('does not read the role from inside the auth callback', async () => {
  // supabase-js holds an auth lock while dispatching this callback, and
  // `apiFetch` calls `getSession()`, so calling it here would deadlock. The
  // read must happen in an effect the callback only schedules.
  renderAuth()
  await screen.findByText('Sign out')
  apiFetch.mockClear()

  authCallback('SIGNED_IN', SESSION('teacher'))
  expect(apiFetch).not.toHaveBeenCalled()

  // ...and it does happen, just afterwards.
  await waitFor(() =>
    expect(apiFetch).toHaveBeenCalledWith('/api/profile/me', expect.any(Object)))
})

it('does not re-read the role when a token refresh replaces the session', async () => {
  // A lesson can outlive an access token, so re-fetching on every refresh
  // would put the app back through a loading state mid-session.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockResolvedValue({ role: 'teacher' })
  render(<AuthProvider><RoleProbe /></AuthProvider>)
  await screen.findByText('role:teacher')
  const before = apiFetch.mock.calls.length

  authCallback('TOKEN_REFRESHED', SESSION('teacher'))

  await waitFor(() => expect(screen.getByText('role:teacher')).toBeInTheDocument())
  expect(apiFetch.mock.calls.length).toBe(before)
})
