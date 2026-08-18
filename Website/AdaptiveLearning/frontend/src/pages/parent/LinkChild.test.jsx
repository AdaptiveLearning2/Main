import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

const toastError = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) },
}))

const navigate = vi.fn()
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import ParentLinkChild from './LinkChild'

const UUID = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'

beforeEach(() => {
  resetApi()
  navigate.mockReset()
  toastError.mockReset()
  toastSuccess.mockReset()
  mockApi({ 'POST /api/parent/link-child': () => ({ ok: true, child_name: 'Ada' }) })
})

it('gives the field an accessible name', async () => {
  // The label and the input were not associated, so a screen reader announced
  // an unlabelled text box -- on the one form in the parent flow whose value is
  // a 36-character UUID nobody can retype from memory. Clicking the words did
  // not focus it either.
  render(<ParentLinkChild />)
  const field = screen.getByLabelText(/child's user id/i)
  expect(field).toBeInTheDocument()

  // Found through the field rather than by text: the instructions above the
  // form say "your child's User ID" too, so matching on the words alone picks
  // up a heading that is not a label.
  const label = document.querySelector(`label[for="${field.id}"]`)
  await userEvent.click(label)
  expect(field).toHaveFocus()
})

it('points at the hint that explains the format', () => {
  const field = (render(<ParentLinkChild />),
                 screen.getByLabelText(/child's user id/i))
  const describedBy = field.getAttribute('aria-describedby')
  expect(describedBy).toBeTruthy()
  expect(document.getElementById(describedBy)).toHaveTextContent(/UUID/i)
})

describe('submitting', () => {
  it('links and goes back to the dashboard', async () => {
    render(<ParentLinkChild />)
    await userEvent.type(screen.getByLabelText(/child's user id/i), UUID)
    await userEvent.click(screen.getByRole('button', { name: /link child account/i }))

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    expect(apiFetch).toHaveBeenCalledWith('/api/parent/link-child',
      expect.objectContaining({ method: 'POST', body: { child_id: UUID } }))
    expect(navigate).toHaveBeenCalledWith('/parent')
  })

  it('stays put and says so when the link is refused', async () => {
    // A 404 here is the ordinary case -- a mistyped UUID -- so navigating away
    // on it would leave a parent on the dashboard wondering whether it worked.
    overrideApi('/api/parent/link-child', () => { throw apiError(404, 'Child account not found') },
                'POST')
    render(<ParentLinkChild />)
    await userEvent.type(screen.getByLabelText(/child's user id/i), UUID)
    await userEvent.click(screen.getByRole('button', { name: /link child account/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(navigate).not.toHaveBeenCalled()
  })
})
