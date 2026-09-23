/**
 * Linking sends the code the child made, not an id about the child.
 *
 * The page used to ask for the child's user id and tell the parent to have the
 * child read it out -- but that id is on every roster payload a teacher of that
 * child reads and in the URL of every report page about them, so possession was
 * never the handover the instructions described.
 */
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

const navigate = vi.fn()
vi.mock('react-router-dom', async () => ({
  ...await vi.importActual('react-router-dom'),
  useNavigate: () => navigate,
}))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import ParentLinkChild from './LinkChild'

const draw = () => render(<MemoryRouter><ParentLinkChild /></MemoryRouter>)

beforeEach(() => {
  resetApi()
  navigate.mockReset()
  toastError.mockReset()
  toastSuccess.mockReset()
  mockApi({ 'POST /api/parent/link-child': () => ({ ok: true, child_name: 'Ada' }) })
})

const type = async (value) => {
  await userEvent.type(screen.getByLabelText(/link code/i), value)
  await userEvent.click(screen.getByRole('button', { name: /link child account/i }))
}

it('sends the code, and nothing that identifies the child', async () => {
  draw()

  await type('abcd2345')

  await waitFor(() => expect(apiFetch).toHaveBeenCalled())
  const [, options] = apiFetch.mock.calls.find(([p]) => p === '/api/parent/link-child')
  // Upper-cased: the alphabet has no lowercase in it, so refusing a typed `a`
  // would be a puzzle rather than a safeguard.
  expect(options.body).toEqual({ link_code: 'ABCD2345' })
  // The old field is the whole point of the change -- a request still carrying
  // it would link on a value the child never had to hand over.
  expect(options.body).not.toHaveProperty('child_id')
})

it('names the child it linked to, from the answer rather than from the input', async () => {
  draw()

  await type('ABCD2345')

  await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
  expect(toastSuccess.mock.calls[0][0]).toMatch(/Ada/)
  expect(navigate).toHaveBeenCalledWith('/parent')
})

it('shows the backend\'s reason for a refused code and stays on the page', async () => {
  // "not valid or has expired" is one message on purpose: which of the two it
  // is would be information about somebody else's account.
  // `overrideApi` takes the method as its third argument, not as a prefix on
  // the path the way `mockApi`'s keys do -- prefixed, it registers a route
  // whose match string never fires, and the happy path answers instead.
  overrideApi('/api/parent/link-child', () => {
    throw apiError(404, 'That code is not valid or has expired. Ask your child to create a new one.')
  }, 'POST')
  draw()

  await type('ABCD2345')

  await waitFor(() => expect(toastError).toHaveBeenCalled())
  expect(toastError.mock.calls[0][0]).toMatch(/not valid or has expired/)
  expect(navigate).not.toHaveBeenCalled()
})

it('tells the parent the child will know', async () => {
  // "Notify, not block" is the recorded decision, so the page that creates the
  // link has to say the notice happens rather than leaving it a surprise.
  draw()

  expect(screen.getByText(/your child will be told/i)).toBeInTheDocument()
})
