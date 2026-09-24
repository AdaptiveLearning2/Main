import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi } from 'vitest'
import RoleGuard from './RoleGuard'
import { homeFor, HOME_BY_ROLE } from '../../lib/homeRoute'

// A refused user goes to their own home, from one role-to-route map.

let auth = { user: { id: 'u1' }, role: 'student', loading: false }
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }))

function renderAt(path, guardRoles) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={
          <RoleGuard roles={guardRoles}><div>protected</div></RoleGuard>
        } />
        {/* Every home, so a redirect to any of them is observable. */}
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
  // A redirect must not land a parent on a route guarded away from them.
  auth = { user: { id: 'p1' }, role: 'parent', loading: false }

  renderAt('/dashboard', ['student'])

  expect(await screen.findByText('parent home')).toBeInTheDocument()
  expect(screen.queryByText('student home')).not.toBeInTheDocument()
})

it('never redirects a role to a route that role may not see', () => {
  // Each home must be reachable by its role, or that role loops.
  for (const [role, home] of Object.entries(HOME_BY_ROLE)) {
    auth = { user: { id: 'x' }, role, loading: false }
    const { unmount } = renderAt(home, [role])
    expect(screen.getByText('protected')).toBeInTheDocument()
    unmount()
  }
})

it('explains itself for an unrecognised role rather than bouncing', () => {
  // No role, no home: guessing one loops, so explain instead.
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
  // During load `role` is undefined; redirecting then logs users out at random.
  auth = { user: null, role: null, loading: true }
  renderAt('/dashboard', ['student'])
  expect(screen.queryByText('login')).not.toBeInTheDocument()
  expect(screen.queryByText('protected')).not.toBeInTheDocument()
})

it('has no home for a role it does not know', () => {
  // A role genuinely not in the map, not just `undefined`.
  expect(homeFor('librarian')).toBeNull()
  expect(homeFor(undefined)).toBeNull()
  expect(homeFor(null)).toBeNull()
})

it('knows where each real role lives', () => {
  // A new role without an entry fails here.
  expect(homeFor('student')).toBe('/dashboard')
  expect(homeFor('teacher')).toBe('/teacher')
  expect(homeFor('parent')).toBe('/parent')
  expect(homeFor('admin')).toBe('/admin')
})
