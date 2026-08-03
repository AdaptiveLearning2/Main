import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

// The facial-reporting preference is stored in localStorage, which is scoped to
// the browser rather than the account. Every route out of a session has to drop
// it, or the next person to sign in on a shared machine inherits a privacy
// setting they never chose.

const signOut = vi.fn()
let authCallback

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: () => Promise.resolve({ data: { session: null } }),
      onAuthStateChange: (cb) => {
        authCallback = cb
        return { data: { subscription: { unsubscribe: () => {} } } }
      },
      signOut: (...args) => signOut(...args),
    },
  },
}))

function SignOutButton() {
  const { signOut: doSignOut } = useAuth()
  return <button onClick={() => doSignOut().catch(() => {})}>Sign out</button>
}

function renderAuth() {
  return render(<AuthProvider><SignOutButton /></AuthProvider>)
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('signal_include_face', 'false')
  signOut.mockReset()
  signOut.mockResolvedValue({ error: null })
})

it('clears the facial-reporting preference on sign-out', async () => {
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('signal_include_face')).toBeNull())
})

it('clears it even when sign-out fails', async () => {
  // A sign-out that fails on the network still leaves the user at the login
  // screen, so the preference must not survive it.
  signOut.mockRejectedValue(new Error('offline'))
  renderAuth()
  await userEvent.click(await screen.findByText('Sign out'))
  await waitFor(() => expect(localStorage.getItem('signal_include_face')).toBeNull())
})

it('clears it on a sign-out this tab did not perform', async () => {
  // An expired refresh token, or a sign-out in another tab, never goes through
  // signOut() above -- it arrives as an auth state change.
  renderAuth()
  await screen.findByText('Sign out')
  authCallback('SIGNED_OUT', null)
  await waitFor(() => expect(localStorage.getItem('signal_include_face')).toBeNull())
})

it('leaves it alone while the session is live', async () => {
  renderAuth()
  await screen.findByText('Sign out')
  authCallback('TOKEN_REFRESHED', { user: { id: 'u1', user_metadata: { role: 'parent' } } })
  expect(localStorage.getItem('signal_include_face')).toBe('false')
})
