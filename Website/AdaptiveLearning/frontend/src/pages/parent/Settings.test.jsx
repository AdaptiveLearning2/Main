import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const toastError = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) },
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'p-1', email: 'parent@example.com' } }),
}))

// The consent panels fetch four endpoints of their own and have their own
// file. Stubbed so this one is about the page around them.
vi.mock('../../components/consent/ConsentChannels', () => ({
  default: ({ studentId }) => <div data-testid={`consent-${studentId}`} />,
}))

import { apiFetch, mockApi, overrideApi, resetApi, apiError, pending } from '../../test/mocks/apiFetch'
import ParentSettings from './Settings'

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
    // The whole subject of this page is what may be measured, and it was the
    // one reading `face_signals` for every linked child to print their names.
    // The rule is that the opt-out skips the query, not that the values are
    // dropped on the way out -- so this asserts on the request.
    render(<ParentSettings />)
    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    const urls = apiFetch.mock.calls.map(c => String(c[0]))
    const childrenCall = urls.find(u => u.includes('/api/parent/children'))
    expect(childrenCall).toContain('include_face=false')
  })
})

describe('the account section', () => {
  it('shows the stored name, the email and the joined date', async () => {
    render(<ParentSettings />)
    expect(await screen.findByDisplayValue('Rae')).toBeInTheDocument()
    expect(screen.getByText('parent@example.com')).toBeInTheDocument()
    expect(screen.getByText(/Joined/)).toBeInTheDocument()
  })

  it('saves the display name', async () => {
    render(<ParentSettings />)
    const field = await screen.findByLabelText(/display name/i)
    // The input renders immediately but is `disabled` until the profile
    // arrives, and findByLabelText resolves as soon as the label exists --
    // so without this wait, clear() lands on a disabled input and throws
    // "clear() is only supported on editable elements". It passes locally
    // and fails on a slower CI runner, which is the worst kind of flake.
    await waitFor(() => expect(field).toBeEnabled())
    await userEvent.clear(field)
    await userEvent.type(field, 'Rae Okafor')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    expect(apiFetch).toHaveBeenCalledWith('/api/profile/me',
      expect.objectContaining({ method: 'PUT', body: { display_name: 'Rae Okafor' } }))
  })

  it('says so when the save fails, instead of reporting success', async () => {
    overrideApi('/api/profile/me', () => { throw apiError(500) }, 'PUT')
    render(<ParentSettings />)
    await screen.findByDisplayValue('Rae')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
})

describe('unlinking a child', () => {
  it('asks first, and does nothing until the second click', async () => {
    // A link is what `_verify_can_view_student` reads as entitlement to a
    // child's reports. One stray click should not end it.
    render(<ParentSettings />)
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])

    expect(apiFetch).not.toHaveBeenCalledWith(expect.stringContaining('/api/parent/children/'),
      expect.objectContaining({ method: 'DELETE' }))
    expect(screen.getByRole('button', { name: /yes, unlink/i })).toBeInTheDocument()
  })

  it('unlinks on confirmation and drops the child from the page', async () => {
    render(<ParentSettings />)
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /yes, unlink/i }))

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/api/parent/children/kid-1',
        expect.objectContaining({ method: 'DELETE' })))
    // Named twice on the page -- once in the list above, once as the heading
    // of their consent panel -- so both have to go.
    await waitFor(() => expect(screen.queryAllByText('Ada')).toHaveLength(0))
    expect(screen.queryAllByText('Basil').length).toBeGreaterThan(0)
  })

  it('keeps the child on the page when the unlink fails', async () => {
    // The removal waits for the response rather than being optimistic: a child
    // vanishing from this list is exactly what a successful unlink looks like,
    // so drawing it before the request lands would report one that did not
    // happen.
    overrideApi('/api/parent/children/kid-1', () => { throw apiError(500) }, 'DELETE')
    render(<ParentSettings />)
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /yes, unlink/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0)
  })

  it('can be backed out of', async () => {
    render(<ParentSettings />)
    const rows = await screen.findAllByRole('button', { name: /^unlink$/i })
    await userEvent.click(rows[0])
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByRole('button', { name: /yes, unlink/i })).not.toBeInTheDocument()
    expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0)
  })
})

describe('when the children read fails', () => {
  it('says so rather than showing an empty family', async () => {
    // "No children linked yet" is a claim about this parent's account. A failed
    // request has not earned it -- the same three-state rule the reporting
    // helpers carry `retrieved` for.
    overrideApi(p => String(p).includes('/api/parent/children'),
                () => { throw apiError(500) })
    render(<ParentSettings />)
    expect(await screen.findByText(/couldn't load your children/i)).toBeInTheDocument()
    expect(screen.queryByText(/no children linked yet/i)).not.toBeInTheDocument()
  })

  it('offers a retry that works', async () => {
    let fail = true
    overrideApi(p => String(p).includes('/api/parent/children'),
                () => { if (fail) throw apiError(500); return CHILDREN })
    render(<ParentSettings />)
    await screen.findByText(/couldn't load your children/i)
    fail = false
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => expect(screen.queryAllByText('Ada').length).toBeGreaterThan(0))
  })

  it('shows a skeleton while it waits, not an empty family', async () => {
    overrideApi(p => String(p).includes('/api/parent/children'), pending())
    render(<ParentSettings />)
    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(screen.queryByText(/no children linked yet/i)).not.toBeInTheDocument()
  })
})
