import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

// The facial-reporting preference is stored in localStorage, which is scoped to
// the browser rather than the account. Every route out of a session has to drop
// it, or the next person to sign in on a shared machine inherits a privacy
// setting they never chose.

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
  // A sign-out that fails on the network still leaves the user at the login
  // screen, so the preference must not survive it.
  signOut.mockRejectedValue(new Error('offline'))
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('clears it on a sign-out this tab did not perform', async () => {
  // An expired refresh token, or a sign-out in another tab, never goes through
  // signOut() above -- it arrives as an auth state change.
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
  // The bug this fixes: an account promoted in the SQL editor has no `role` in
  // its metadata at all, so it rendered as a student -- student nav, a badge
  // reading "Student", and no way to reach the console it administers.
  getSession.mockResolvedValue({ data: { session: SESSION(null) } })
  apiFetch.mockResolvedValue({ role: 'admin' })

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:admin')).toBeInTheDocument()
  expect(apiFetch).toHaveBeenCalledWith('/api/profile/me')
})

it('believes the backend over a claim that disagrees', async () => {
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockResolvedValue({ role: 'student' })

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:student')).toBeInTheDocument()
})

it('says loading until the role has actually resolved', async () => {
  // Otherwise the guards see `role === null` for a frame and render the
  // "this account isn't set up" screen on every page load.
  //
  // The window that matters is *after* the session resolves and *before* the
  // profile does. Asserting "loading" right after render proves nothing --
  // `authLoading` is true then whatever this does, which is how the first
  // version of this test passed against a provider that still had the bug.
  let resolve
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockReturnValue(new Promise(r => { resolve = r }))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  // Flush the session promise, leaving only the profile read outstanding.
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  expect(screen.queryByText('role:null')).not.toBeInTheDocument()
  expect(screen.getByText('loading')).toBeInTheDocument()

  resolve({ role: 'teacher' })
  expect(await screen.findByText('role:teacher')).toBeInTheDocument()
})

it('falls back to the claim when the backend cannot be reached', async () => {
  // A blip is not a demotion. Falling back to 'student' would drop every
  // teacher into the wrong application whenever the API was down.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockRejectedValue(new Error('offline'))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:teacher')).toBeInTheDocument()
})

it('does not read the role from inside the auth callback', async () => {
  // supabase-js holds an internal auth lock while dispatching, and `apiFetch`
  // calls `getSession()` for the token -- so awaiting it there deadlocks and
  // the app hangs on a loader for ever. The read must happen in an effect the
  // callback merely schedules, which is what this pins: nothing calls apiFetch
  // during the dispatch itself.
  renderAuth()
  await screen.findByText('Sign out')
  apiFetch.mockClear()

  authCallback('SIGNED_IN', SESSION('teacher'))
  expect(apiFetch).not.toHaveBeenCalled()

  // ...and it does still happen, immediately afterwards.
  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/api/profile/me'))
})

it('does not re-read the role when a token refresh replaces the session', async () => {
  // The effect is keyed on the user id for this reason: a lesson can outlive an
  // access token, and re-fetching on every refresh would put the whole app back
  // through a loading state mid-session.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockResolvedValue({ role: 'teacher' })
  render(<AuthProvider><RoleProbe /></AuthProvider>)
  await screen.findByText('role:teacher')
  const before = apiFetch.mock.calls.length

  authCallback('TOKEN_REFRESHED', SESSION('teacher'))

  await waitFor(() => expect(screen.getByText('role:teacher')).toBeInTheDocument())
  expect(apiFetch.mock.calls.length).toBe(before)
})
