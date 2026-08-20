import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const toastError = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) },
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'p-1', email: 'parent@example.com' } }),
}))

// Stubbed so this file tests the page around the consent panels, not the
// panels themselves — they have their own test file.
vi.mock('../../components/consent/ConsentChannels', () => ({
  default: ({ studentId }) => <div data-testid={`consent-${studentId}`} />,
}))

import { apiFetch, mockApi, overrideApi, resetApi, apiError, pending } from '../../test/mocks/apiFetch'
import ParentSettings from './Settings'

// The children section links to /parent/link, so the page needs a router.
const draw = () => render(<MemoryRouter><ParentSettings /></MemoryRouter>)

const CHILDREN = [
  { user_id: 'kid-1', name: 'Ada',   email: 'ada@example.com',  linked_at: '2026-02-03T10:00:00Z' },
  { user_id: 'kid-2', name: 'Basil', email: 'basil@example.com', linked_at: '2026-05-09T10:00:00Z' },
]

beforeEach(() => {
  resetApi()
  toastError.mockReset()
  toastSuccess.mockReset()
  mockApi({
    '/api/parent/children?include_face=false': () => CHILDREN,
    '/api/profile/me': () => ({ display_name: 'Rae', created_at: '2025-09-01T00:00:00Z' }),
    'PUT /api/profile/me': () => ({ display_name: 'Rae' }),
    'DELETE /api/parent/children/kid-1': () => ({ ok: true, child_id: 'kid-1' }),
  })
})

describe('the facial opt-out', () => {
  it('does not read facial data for a page that renders none', async () => {
    // The opt-out must skip the query entirely, not just drop the values
    // on the way out.
    draw()
    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    const urls = apiFetch.mock.calls.map(c => String(c[0]))
    const childrenCall = urls.find(u => u.includes('/api/parent/children'))
    expect(childrenCall).toContain('include_face=false')
  })
})

describe('the account section', () => {
  it('shows the stored name, the email and the joined date', async () => {
    draw()
    expect(await screen.findByDisplayValue('Rae')).toBeInTheDocument()
    expect(screen.getByText('parent@example.com')).toBeInTheDocument()
    expect(screen.getByText(/Joined/)).toBeInTheDocument()
  })

  it('saves the display name', async () => {
    draw()
    // Wait for the value, not just presence: `findByLabelText` resolves as
    // soon as the (still-disabled) input exists, before the fetch settles,
    // and `userEvent.clear()` throws on a disabled element.
    await screen.findByDisplayValue('Rae')
    const field = screen.getByLabelText(/display name/i)
    await userEvent.clear(field)
    await userEvent.type(field, 'Rae Okafor')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    expect(apiFetch).toHaveBeenCalledWith('/api/profile/me',
      expect.objectContaining({ method: 'PUT', body: { display_name: 'Rae Okafor' } }))
  })

  it('says so when the save fails, instead of reporting success', async () => {
    overrideApi('/api/profile/me', () => { throw apiError(500) }, 'PUT')
    draw()
    await screen.findByDisplayValue('Rae')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
})

describe('unlinking a child', () => {
  it('asks first, and does nothing until the second click', async () => {
    draw()
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])

    expect(apiFetch).not.toHaveBeenCalledWith(expect.stringContaining('/api/parent/children/'),
      expect.objectContaining({ method: 'DELETE' }))
    expect(screen.getByRole('button', { name: /yes, unlink/i })).toBeInTheDocument()
  })

  it('unlinks on confirmation and drops the child from the page', async () => {
    draw()
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /yes, unlink/i }))

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/api/parent/children/kid-1',
        expect.objectContaining({ method: 'DELETE' })))
    // Named twice on the page: the list row and the consent panel heading.
    await waitFor(() => expect(screen.queryAllByText('Ada')).toHaveLength(0))
    expect(screen.queryAllByText('Basil').length).toBeGreaterThan(0)
  })

  it('keeps the child on the page when the unlink fails', async () => {
    // Removal waits for the response rather than being optimistic.
    overrideApi('/api/parent/children/kid-1', () => { throw apiError(500) }, 'DELETE')
    draw()
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /yes, unlink/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0)
  })

  it('can be backed out of', async () => {
    draw()
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByRole('button', { name: /yes, unlink/i })).not.toBeInTheDocument()
    expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0)
  })
})

describe('when the children read fails', () => {
  it('says so rather than showing an empty family', async () => {
    // "No children linked yet" is a claim a failed request hasn't earned.
    overrideApi(p => String(p).includes('/api/parent/children'),
                () => { throw apiError(500) })
    draw()
    expect(await screen.findByText(/couldn't load your children/i)).toBeInTheDocument()
    expect(screen.queryByText(/no children linked yet/i)).not.toBeInTheDocument()
  })

  it('offers a retry that works', async () => {
    let fail = true
    overrideApi(p => String(p).includes('/api/parent/children'),
                () => { if (fail) throw apiError(500); return CHILDREN })
    draw()
    await screen.findByText(/couldn't load your children/i)
    fail = false
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0))
  })

  it('shows a skeleton while it waits, not an empty family', async () => {
    overrideApi(p => String(p).includes('/api/parent/children'), pending())
    draw()
    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(screen.queryByText(/no children linked yet/i)).not.toBeInTheDocument()
  })
})

it('offers a way to link another child', async () => {
  draw()
  // Named twice: the list row and their consent panel's heading.
  await screen.findAllByText('Ada')
  expect(screen.getByRole('link', { name: /link another child/i }))
    .toHaveAttribute('href', '/parent/link')
})

it('offers it from the empty state too, which is where it matters most', async () => {
  overrideApi(p => String(p).includes('/api/parent/children'), () => [])
  draw()
  await screen.findByText(/no children linked yet/i)
  expect(screen.getByRole('link', { name: /link another child/i })).toBeInTheDocument()
})
