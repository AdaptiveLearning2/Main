/** Linking sends the code the child made, not the child's id, which is not a secret. */
import { it, expect, beforeEach, vi } from 'vitest'
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
  // Upper-cased: the code alphabet has no lowercase.
  expect(options.body).toEqual({ link_code: 'ABCD2345' })
  // No child id in the request.
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
  // One message, so it leaks nothing; `overrideApi` takes the method as its third argument.
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
  // "Notify, not block": the page says the child is told.
  draw()

  expect(screen.getByText(/your child will be told/i)).toBeInTheDocument()
})
