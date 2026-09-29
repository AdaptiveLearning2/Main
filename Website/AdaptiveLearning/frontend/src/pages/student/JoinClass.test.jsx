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

  it('lists a joined class by name and grade, with no class code', async () => {
    // An older backend still sent the code; the page must not show it either way.
    mockApi({ '/api/classes': [{ id: 'c1', name: 'Algebra', grade_level: '7th Grade', join_code: 'ABCD2345' }] })
    render(<JoinClass />)
    expect(await screen.findByText('Algebra')).toBeInTheDocument()
    expect(screen.getByText('7th Grade')).toBeInTheDocument()
    expect(screen.queryByText('ABCD2345')).not.toBeInTheDocument()
    expect(screen.queryByText(/Code:/)).not.toBeInTheDocument()
  })

  it('offers no join for a length no code has', async () => {
    render(<JoinClass />)
    await userEvent.type(screen.getByLabelText('Class code'), 'abcd234')
    expect(screen.getByRole('button', { name: 'Join Class' })).toBeDisabled()
  })
})
