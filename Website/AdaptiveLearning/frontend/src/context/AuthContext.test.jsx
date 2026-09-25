import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'
import { onSignOut } from '../lib/signOutTasks'

// The display preference is per browser, so every sign-out path clears it for shared machines.

const signOut = vi.fn()
const getSession = vi.fn()
const apiFetch = vi.fn()
const authSignUp = vi.fn(async () => ({ error: null }))
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
      signUp: (...args) => authSignUp(...args),
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

/** Renders the resolved display name, once loading is done. */
function NameProbe() {
  const { displayName, loading, refreshProfile } = useAuth()
  return (
    <div>
      <span>{loading ? 'loading' : `name:${displayName}`}</span>
      <button onClick={() => refreshProfile()}>refresh</button>
    </div>
  )
}

const NAMED = (metadata) => ({ user: { id: 'u1', email: 'ada.lovelace@school.org',
                                       user_metadata: metadata || {} } })

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

it("sends a student's grade with the sign-up, and no grade for anyone else", async () => {
  let signUp
  function Grab() { signUp = useAuth().signUp; return null }
  render(<AuthProvider><Grab /></AuthProvider>)
  await waitFor(() => expect(signUp).toBeTypeOf('function'))

  await act(() => signUp('kid@school.org', 'pw123456', 'student', 'Kid', '5th Grade'))
  await act(() => signUp('t@school.org', 'pw123456', 'teacher', 'T', '5th Grade'))

  const data = authSignUp.mock.calls.map(([arg]) => arg.options.data)
  expect(data[0]).toEqual({ role: 'student', display_name: 'Kid', grade_level: '5th Grade' })
  expect(data[1]).toEqual({ role: 'teacher', display_name: 'T' })
})

it('clears the teacher display preference on sign-out', async () => {
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('clears it even when sign-out fails', async () => {
  signOut.mockRejectedValue(new Error('offline'))
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('teacher_hide_sensor_data')).toBeNull())
})

it('runs the sign-out tasks while the token still exists', async () => {
  // Asserted as an order, since both calls happen either way.
  const order = []
  signOut.mockImplementation(async () => { order.push('signOut'); return { error: null } })
  const off = onSignOut(async () => { order.push('task') })
  try {
    renderAuth()
    await userEvent.click(await screen.findByText('Sign out'))
    await waitFor(() => expect(order).toEqual(['task', 'signOut']))
  } finally {
    off()
  }
})

it('joins a sign-out already in progress rather than starting a second', async () => {
  // A slow task leaves the button live; a second click must not rerun the tasks.
  let release
  const task = vi.fn(() => new Promise(r => { release = r }))
  const off = onSignOut(task)
  try {
    renderAuth()
    const button = await screen.findByText('Sign out')
    await userEvent.click(button)
    await userEvent.click(button)
    release()
    await waitFor(() => expect(signOut).toHaveBeenCalledTimes(1))
    expect(task).toHaveBeenCalledTimes(1)
  } finally {
    off()
  }
})

it('clears it on a sign-out this tab did not perform', async () => {
  // An expired refresh token or another tab's sign-out arrives as an auth event.
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
  // An account promoted in the database has no role in its metadata.
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
  // The window that matters is after the session resolves, before the profile does.
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
  // A `.catch` is not a bound: a hung request never rejects.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockReturnValue(new Promise(() => {}))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const [, opts] = apiFetch.mock.calls[0]
  expect(opts?.timeoutMs).toBeGreaterThan(0)
})

it('falls back to the claim when the backend cannot be reached', async () => {
  // A blip is not a demotion.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockRejectedValue(new Error('offline'))

  render(<AuthProvider><RoleProbe /></AuthProvider>)

  expect(await screen.findByText('role:teacher')).toBeInTheDocument()
})

it('does not read the role from inside the auth callback', async () => {
  // supabase-js holds an auth lock during the callback and `apiFetch` calls `getSession()`: deadlock.
  renderAuth()
  await screen.findByText('Sign out')
  apiFetch.mockClear()

  authCallback('SIGNED_IN', SESSION('teacher'))
  expect(apiFetch).not.toHaveBeenCalled()

  // It happens afterwards, in an effect.
  await waitFor(() =>
    expect(apiFetch).toHaveBeenCalledWith('/api/profile/me', expect.any(Object)))
})

it('does not re-read the role when a token refresh replaces the session', async () => {
  // Keyed on user id: a mid-lesson refresh must not re-enter loading.
  getSession.mockResolvedValue({ data: { session: SESSION('teacher') } })
  apiFetch.mockResolvedValue({ role: 'teacher' })
  render(<AuthProvider><RoleProbe /></AuthProvider>)
  await screen.findByText('role:teacher')
  const before = apiFetch.mock.calls.length

  authCallback('TOKEN_REFRESHED', SESSION('teacher'))

  await waitFor(() => expect(screen.getByText('role:teacher')).toBeInTheDocument())
  expect(apiFetch.mock.calls.length).toBe(before)
})


/** `displayName`: stored name, then claim, then email prefix. */
it('names a user from their stored profile, not from their email', async () => {
  getSession.mockResolvedValue({ data: { session: NAMED() } })
  apiFetch.mockResolvedValue({ role: 'student', display_name: 'Ada' })
  render(<AuthProvider><NameProbe /></AuthProvider>)

  expect(await screen.findByText('name:Ada')).toBeInTheDocument()
})

it('falls back to the claim, then the email, when the profile has no name', async () => {
  // The backend mirrors the stored name into user_metadata on every save.
  getSession.mockResolvedValue({ data: { session: NAMED({ display_name: 'Ada L' }) } })
  apiFetch.mockResolvedValue({ role: 'student', display_name: null })
  const { unmount } = render(<AuthProvider><NameProbe /></AuthProvider>)
  expect(await screen.findByText('name:Ada L')).toBeInTheDocument()
  unmount()

  getSession.mockResolvedValue({ data: { session: NAMED() } })
  apiFetch.mockResolvedValue({ role: 'student' })
  render(<AuthProvider><NameProbe /></AuthProvider>)
  expect(await screen.findByText('name:ada.lovelace')).toBeInTheDocument()
})

it('treats a blank stored name as no name at all', async () => {
  getSession.mockResolvedValue({ data: { session: NAMED() } })
  apiFetch.mockResolvedValue({ role: 'student', display_name: '   ' })
  render(<AuthProvider><NameProbe /></AuthProvider>)

  expect(await screen.findByText('name:ada.lovelace')).toBeInTheDocument()
})

it('keeps a name on a failed read rather than showing none', async () => {
  // Same direction as the role.
  getSession.mockResolvedValue({ data: { session: NAMED({ display_name: 'Ada L' }) } })
  apiFetch.mockRejectedValue(new Error('offline'))
  render(<AuthProvider><NameProbe /></AuthProvider>)

  expect(await screen.findByText('name:Ada L')).toBeInTheDocument()
})

it('re-reads the name after a save, instead of waiting for a reload', async () => {
  // Profile calls `refreshProfile` after a successful save.
  getSession.mockResolvedValue({ data: { session: NAMED() } })
  apiFetch.mockResolvedValue({ role: 'student', display_name: 'Ada' })
  render(<AuthProvider><NameProbe /></AuthProvider>)
  await screen.findByText('name:Ada')

  apiFetch.mockResolvedValue({ role: 'student', display_name: 'Ada Lovelace' })
  await userEvent.click(screen.getByText('refresh'))

  expect(await screen.findByText('name:Ada Lovelace')).toBeInTheDocument()
})
