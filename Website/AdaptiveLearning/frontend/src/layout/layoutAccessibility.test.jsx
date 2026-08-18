import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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


// ── the mobile drawer ───────────────────────────────────────────────────────
//
// All four layouts carried a byte-identical copy of it and all four were
// missing the same three things, so these run over every one: the point of
// extracting `MobileDrawer` is that there is now one implementation, and the
// point of parametrising is to catch a layout that stops using it.

describe.each(LAYOUTS)('%s mobile drawer', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  const open = async (Layout, path) => {
    renderLayout(Layout, path)
    await userEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    return screen.getByRole('dialog')
  }

  it('announces itself as a modal dialog', async () => {
    // Without this a screen reader reads the page behind as though it were
    // still available -- the same confusion the focus trap prevents for a
    // sighted keyboard user.
    const dialog = await open(Layout, path)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName()
  })

  it('closes on Escape', async () => {
    // The backdrop was the only way out, so a keyboard user who opened the
    // menu had none -- on the control that is the whole of the navigation on a
    // phone.
    await open(Layout, path)
    await userEvent.keyboard('{Escape}')
    // Longer than the default 1s, because what this waits on is a spring exit
    // animation and not a decision: `AnimatePresence` keeps the node mounted
    // until it finishes, which is ~600ms on an idle machine and over a second
    // when the rest of the suite is running beside it. It passed at the default
    // in isolation on all four layouts and failed on two of them in the full
    // run, which is the signature of a timeout on animation rather than a bug.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
                  { timeout: 4000 })
  })

  it('moves focus into the drawer when it opens', async () => {
    const dialog = await open(Layout, path)
    await waitFor(() => expect(dialog).toContainElement(document.activeElement))
  })

  it('gives focus back to the opener when it closes', async () => {
    // Closing dropped focus to the top of the document, so a reader lost their
    // place every time they opened the menu and changed their mind.
    renderLayout(Layout, path)
    const opener = screen.getByRole('button', { name: 'Open menu' })
    await userEvent.click(opener)
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: 'Close menu' }))
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('keeps Tab inside it', async () => {
    // Otherwise tabbing walks into the page behind, which is still there and
    // still focusable under an opaque overlay, with nothing to say focus left.
    const dialog = await open(Layout, path)

    // Far more presses than the drawer has stops, so it must have wrapped.
    for (let i = 0; i < 25; i += 1) await userEvent.tab()
    expect(dialog).toContainElement(document.activeElement)
  })
})

describe.each(LAYOUTS)('%s sidebar collapse', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  it('remembers the choice across a remount', async () => {
    // It was `useState(false)`, which survives navigation -- the layout stays
    // mounted under its child routes -- and not a refresh. So a teacher who
    // collapsed it for width had it back at 240px on every page load.
    const first = renderLayout(Layout, path)
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    await screen.findByRole('button', { name: 'Expand sidebar' })
    first.unmount()

    renderLayout(Layout, path)
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
  })

  it('starts expanded when nothing has been stored', () => {
    renderLayout(Layout, path)
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
  })
})
