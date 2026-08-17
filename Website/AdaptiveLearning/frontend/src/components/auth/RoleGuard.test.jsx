import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi } from 'vitest'
import RoleGuard from './RoleGuard'
import { homeFor, HOME_BY_ROLE } from '../../lib/homeRoute'

// The guard sends a user who reached a route they may not see back to their own
// home. That destination used to be `role === 'teacher' ? '/teacher' :
// '/dashboard'`, which is an infinite loop for a parent: `/dashboard` is
// student-only, so the guard rejects it and computes `/dashboard` again. A
// parent following a stale link, or landing on `/`, bounced until the browser
// stopped it.

let auth = { user: { id: 'u1' }, role: 'student', loading: false }
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }))

function renderAt(path, guardRoles) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={
          <RoleGuard roles={guardRoles}><div>protected</div></RoleGuard>
        } />
        {/* Every home, so a redirect to any of them is observable rather than
            rendering blank -- a blank page is what the loop looked like. */}
        <Route path="/dashboard" element={<div>student home</div>} />
        <Route path="/teacher"   element={<div>teacher home</div>} />
        <Route path="/parent"    element={<div>parent home</div>} />
        <Route path="/login"     element={<div>login</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { auth = { user: { id: 'u1' }, role: 'student', loading: false } })

it('sends a parent to the parent home, not the student one', async () => {
  // The regression. Before the fix this rendered "student home", and in the
  // real router that route is itself guarded -- so it bounced back here.
  auth = { user: { id: 'p1' }, role: 'parent', loading: false }

  renderAt('/dashboard', ['student'])

  expect(await screen.findByText('parent home')).toBeInTheDocument()
  expect(screen.queryByText('student home')).not.toBeInTheDocument()
})

it('never redirects a role to a route that role may not see', () => {
  // The property behind the bug, stated directly: every home this map hands
  // out has to be reachable by the role it is handed to. A future fourth role
  // pointed at someone else's home reproduces the loop exactly.
  for (const [role, home] of Object.entries(HOME_BY_ROLE)) {
    auth = { user: { id: 'x' }, role, loading: false }
    const { unmount } = renderAt(home, [role])
    expect(screen.getByText('protected')).toBeInTheDocument()
    unmount()
  }
})

it('explains itself for an unrecognised role rather than bouncing', () => {
  // A profile with no role has no home, and picking one anyway is the loop
  // again -- every candidate is guarded. Terminating with a sentence is the
  // only option that cannot cycle.
  auth = { user: { id: 'x' }, role: null, loading: false }

  renderAt('/dashboard', ['student'])

  expect(screen.getByText(/isn't set up yet/i)).toBeInTheDocument()
  expect(screen.queryByText('student home')).not.toBeInTheDocument()
})

it('lets a permitted role through untouched', () => {
  renderAt('/dashboard', ['student'])
  expect(screen.getByText('protected')).toBeInTheDocument()
})

it('sends a signed-out visitor to login', () => {
  auth = { user: null, role: null, loading: false }
  renderAt('/dashboard', ['student'])
  expect(screen.getByText('login')).toBeInTheDocument()
})

it('waits for auth instead of guessing while it loads', () => {
  // Redirecting during load reads `role` as undefined, which for a signed-in
  // parent looks like being logged out at random.
  auth = { user: null, role: null, loading: true }
  renderAt('/dashboard', ['student'])
  expect(screen.queryByText('login')).not.toBeInTheDocument()
  expect(screen.queryByText('protected')).not.toBeInTheDocument()
})

it('has no home for a role it does not know', () => {
  // Was `admin`, which is now a real role with a real home (#125). An example
  // that quietly becomes valid is worse than no example: this assertion would
  // have gone on passing for `undefined` alone while claiming to cover the
  // unknown-role case, which is the case the null-not-a-default rule exists for.
  expect(homeFor('librarian')).toBeNull()
  expect(homeFor(undefined)).toBeNull()
  expect(homeFor(null)).toBeNull()
})

it('knows where each real role lives', () => {
  // Every role the app actually has, so a fifth one added to the routes without
  // an entry here fails rather than sending that user to a route their guard
  // will refuse. `admin` is the one that got in without this: it arrived on a
  // branch that never touched this file, so nothing conflicted and nothing
  // flagged it, and an admin hitting `/` landed on "no role assigned".
  expect(homeFor('student')).toBe('/dashboard')
  expect(homeFor('teacher')).toBe('/teacher')
  expect(homeFor('parent')).toBe('/parent')
  expect(homeFor('admin')).toBe('/admin')
})
