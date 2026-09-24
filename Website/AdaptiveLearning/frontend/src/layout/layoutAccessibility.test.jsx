import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

// Auth and theme context stubbed, so this file tests only the markup.
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'someone@example.com' },
                    displayName: authName, signOut: vi.fn() }),
}))
// Set per test; layouts render the given name rather than deriving one.
let authName = 'Ada Lovelace'
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
  // Icon-only buttons; asserted by accessible name, not `aria-label` directly.
  it.each([
    ['Collapse sidebar'],
    ['Open menu'],
    ['Sign out'],
  ])('gives the %s control an accessible name', (name) => {
    renderLayout(Layout, path)
    expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('names the theme toggle by what pressing it does', () => {
    // The sidebar and mobile top bar each have a copy.
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
// Over every layout, so each uses the shared `MobileDrawer`.

describe.each(LAYOUTS)('%s mobile drawer', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  const open = async (Layout, path) => {
    renderLayout(Layout, path)
    await userEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    return screen.getByRole('dialog')
  }

  it('announces itself as a modal dialog', async () => {
    const dialog = await open(Layout, path)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName()
  })

  it('closes on Escape', async () => {
    await open(Layout, path)
    await userEvent.keyboard('{Escape}')
    // Waits on a spring exit animation, slow under the full suite.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
                  { timeout: 4000 })
  })

  it('moves focus into the drawer when it opens', async () => {
    const dialog = await open(Layout, path)
    await waitFor(() => expect(dialog).toContainElement(document.activeElement))
  })

  it('gives focus back to the opener when it closes', async () => {
    renderLayout(Layout, path)
    const opener = screen.getByRole('button', { name: 'Open menu' })
    await userEvent.click(opener)
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: 'Close menu' }))
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('keeps Tab inside it', async () => {
    const dialog = await open(Layout, path)

    // More presses than the drawer has stops, so focus must wrap.
    for (let i = 0; i < 25; i += 1) await userEvent.tab()
    expect(dialog).toContainElement(document.activeElement)
  })
})

describe.each(LAYOUTS)('%s sidebar collapse', (_name, Layout, path) => {
  beforeEach(() => { localStorage.clear() })

  it('remembers the choice across a remount', async () => {
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
    // A shared key would leak between roles on a shared school machine.
    const teacher = renderLayout(TeacherLayout, '/teacher')
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    await screen.findByRole('button', { name: 'Expand sidebar' })
    teacher.unmount()

    renderLayout(StudentLayout, '/dashboard')
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
  })

  it('gives every layout a distinct key', () => {
    // Read from storage, so a new layout that forgets its scope fails.
    for (const [, Layout, path] of LAYOUTS) {
      renderLayout(Layout, path)
      cleanup()
    }
    const keys = Object.keys(localStorage).filter(k => k.startsWith('al_sidebar_collapsed'))
    expect(new Set(keys).size).toBe(LAYOUTS.length)
  })
})

/** The avatar letter and the name beside it derive from one value. */
describe.each(LAYOUTS)('%s account block', (_name, Layout, path) => {
  // Collapse persists and hides the account block entirely.
  beforeEach(() => { localStorage.clear() })

  // First, so the tests below stand downstream of its leak and give the clear teeth.
  it('is hidden entirely when the sidebar is collapsed', async () => {
    authName = 'Ada Lovelace'
    renderLayout(Layout, path)
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.queryByText('Ada Lovelace')).not.toBeInTheDocument()
  })

  it('takes the avatar letter from the name beside it', async () => {
    authName = 'Ada Lovelace'
    renderLayout(Layout, path)

    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('A')).toBeInTheDocument()
    // Not the email's first letter.
    expect(screen.queryByText('S')).not.toBeInTheDocument()
  })

  it('shows a placeholder rather than a letter of nothing', async () => {
    authName = null
    renderLayout(Layout, path)

    expect(screen.getByText('?')).toBeInTheDocument()
  })
})
