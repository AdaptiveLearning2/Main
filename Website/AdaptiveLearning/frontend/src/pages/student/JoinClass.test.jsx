import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
import { apiFetch, mockApi, resetApi } from '../../test/mocks/apiFetch'
import JoinClass from './JoinClass'

beforeEach(() => {
  resetApi()
  mockApi({ '/api/classes': [], 'POST /api/classes/join': { id: 'c1', name: 'Algebra' } })
})

describe('JoinClass', () => {
  it('takes a new 8-character code and sends all of it', async () => {
    render(<JoinClass />)
    await userEvent.type(screen.getByLabelText('Class code'), 'abcd2345')
    await userEvent.click(screen.getByRole('button', { name: 'Join Class' }))
    expect(apiFetch).toHaveBeenCalledWith('/api/classes/join',
      { method: 'POST', body: { join_code: 'ABCD2345' } })
  })

  it('still takes a 6-character code issued before the longer ones', async () => {
    render(<JoinClass />)
    await userEvent.type(screen.getByLabelText('Class code'), 'abc123')
    expect(screen.getByRole('button', { name: 'Join Class' })).toBeEnabled()
  })

  it('offers no join for a length no code has', async () => {
    render(<JoinClass />)
    await userEvent.type(screen.getByLabelText('Class code'), 'abcd234')
    expect(screen.getByRole('button', { name: 'Join Class' })).toBeDisabled()
  })
})
