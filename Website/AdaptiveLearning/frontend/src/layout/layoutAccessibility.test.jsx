import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

// The layouts are shells: they render a sidebar and an `<Outlet/>`, and they
// reach for auth and theme context. Both are stubbed rather than provided, so
// this file tests the markup and nothing behind it.
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'someone@example.com' }, signOut: vi.fn() }),
}))
vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ dark: false, toggleTheme: vi.fn() }),
}))

import StudentLayout from './StudentLayout'
import TeacherLayout from './TeacherLayout'
import ParentLayout from './ParentLayout'
import AdminLayout from './AdminLayout'

const LAYOUTS = [
  ['StudentLayout', StudentLayout, '/dashboard'],
  ['TeacherLayout', TeacherLayout, '/teacher'],
  ['ParentLayout', ParentLayout, '/parent'],
  ['AdminLayout', AdminLayout, '/admin'],
]

function renderLayout(Layout, path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path={path} element={<p>page body</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe.each(LAYOUTS)('%s', (_name, Layout, path) => {
  // Every one of these is an icon with no text beside it, so without a label a
  // screen reader announces the control as "button" and nothing else. They are
  // the whole of the chrome -- collapsing the sidebar, opening the drawer,
  // switching theme, signing out -- so an unlabelled set leaves a
  // keyboard-and-screen-reader user unable to name any of them.
  //
  // Asserted by accessible name rather than by attribute: `aria-label` is one
  // way to get one, and this should keep passing if a later change gets there
  // by visible text instead.
  it.each([
    ['Collapse sidebar'],
    ['Open menu'],
    ['Sign out'],
  ])('gives the %s control an accessible name', (name) => {
    renderLayout(Layout, path)
    expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('names the theme toggle by what pressing it does', () => {
    // Both copies -- the sidebar's and the mobile top bar's -- carry it, so
    // this finds more than one. `getAllByRole` rather than `getByRole` for that
    // reason: a bare `get` would fail on the duplicate and read as the label
    // being wrong rather than present twice.
    renderLayout(Layout, path)
    expect(screen.getAllByRole('button', { name: 'Switch to dark mode' }).length)
      .toBeGreaterThan(0)
  })

  it('still renders the page it wraps', () => {
    renderLayout(Layout, path)
    expect(screen.getByText('page body')).toBeInTheDocument()
  })
})
