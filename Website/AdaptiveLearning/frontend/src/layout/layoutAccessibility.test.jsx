import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

// The layouts render a sidebar and an `<Outlet/>` and read auth/theme
// context, both stubbed here so this file tests only the markup.
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
  // These are icon-only buttons, so without a label a screen reader
  // announces just "button". Asserted by accessible name, not by
  // `aria-label` directly, so this still passes if the label comes from
  // visible text instead.
  it.each([
    ['Collapse sidebar'],
    ['Open menu'],
    ['Sign out'],
  ])('gives the %s control an accessible name', (name) => {
    renderLayout(Layout, path)
    expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('names the theme toggle by what pressing it does', () => {
    // Both the sidebar and mobile top bar have a copy, so use `getAllByRole`
    // — a plain `getByRole` would fail on the duplicate.
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
// Runs over every layout to make sure each one uses the shared
// `MobileDrawer` implementation, not its own copy.

describe.each(LAYOUTS)('%s mobile drawer', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  const open = async (Layout, path) => {
    renderLayout(Layout, path)
    await userEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    return screen.getByRole('dialog')
  }

  it('announces itself as a modal dialog', async () => {
    // Without this a screen reader reads the page behind as still available.
    const dialog = await open(Layout, path)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName()
  })

  it('closes on Escape', async () => {
    // Without Escape, the backdrop click is the only way to close the menu
    // — no keyboard-only path exists.
    await open(Layout, path)
    await userEvent.keyboard('{Escape}')
    // Longer than the default 1s: this waits on a spring exit animation,
    // which can take over a second when the full suite runs alongside it.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
                  { timeout: 4000 })
  })

  it('moves focus into the drawer when it opens', async () => {
    const dialog = await open(Layout, path)
    await waitFor(() => expect(dialog).toContainElement(document.activeElement))
  })

  it('gives focus back to the opener when it closes', async () => {
    // Focus must return to the opener, or a keyboard user loses their place
    // every time they close the menu.
    renderLayout(Layout, path)
    const opener = screen.getByRole('button', { name: 'Open menu' })
    await userEvent.click(opener)
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: 'Close menu' }))
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('keeps Tab inside it', async () => {
    // Otherwise Tab walks into the page behind the overlay.
    const dialog = await open(Layout, path)

    // More presses than the drawer has stops, to confirm focus wraps.
    for (let i = 0; i < 25; i += 1) await userEvent.tab()
    expect(dialog).toContainElement(document.activeElement)
  })
})

describe.each(LAYOUTS)('%s sidebar collapse', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  it('remembers the choice across a remount', async () => {
    // Must be persisted, not just component state — otherwise it resets on
    // every page load.
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

describe('sidebar collapse is per layout', () => {
  beforeEach(() => { localStorage.clear() })

  it('does not leak the choice from one role to another', async () => {
    // A shared key would leak the collapse state between roles on a shared
    // school machine.
    const teacher = renderLayout(TeacherLayout, '/teacher')
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    await screen.findByRole('button', { name: 'Expand sidebar' })
    teacher.unmount()

    renderLayout(StudentLayout, '/dashboard')
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
  })

  it('gives every layout a distinct key', () => {
    // Reads the keys from storage, not from the components, so a new layout
    // that forgets its own scope fails this test.
    for (const [, Layout, path] of LAYOUTS) {
      renderLayout(Layout, path)
      cleanup()
    }
    const keys = Object.keys(localStorage).filter(k => k.startsWith('al_sidebar_collapsed'))
    expect(new Set(keys).size).toBe(LAYOUTS.length)
  })
})
