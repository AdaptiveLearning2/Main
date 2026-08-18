import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import Classes from './Classes'

const CLASS = { id: 'c-1', name: 'Year 7 Maths', join_code: 'AB12CD', grade_level: '7th Grade' }

const draw = () => render(<MemoryRouter><Classes /></MemoryRouter>)

beforeEach(() => {
  resetApi()
  mockApi({ '/api/classes': () => [CLASS] })
})

describe('the class list', () => {
  it('lists a class', async () => {
    draw()
    expect(await screen.findByText('Year 7 Maths')).toBeInTheDocument()
  })

  it('survives a class whose name is blank', async () => {
    // `''[0]` is undefined and `.toUpperCase()` on it throws during render, so
    // one row like this took out the whole list -- on the first page a teacher
    // opens, and with no error boundary above it at the time. `ClassDetail.jsx`
    // already carries this guard and regression-tests it; this page did not.
    overrideApi('/api/classes', () => ([{ ...CLASS, id: 'c-2', name: '' }, CLASS]))

    draw()

    // The good one still renders, which is what says the list survived rather
    // than the page having been replaced by an error screen.
    expect(await screen.findByText('Year 7 Maths')).toBeInTheDocument()
    expect(screen.getByText('Untitled class')).toBeInTheDocument()
  })

  it('survives a class with no name field at all', async () => {
    overrideApi('/api/classes', () => ([{ id: 'c-3', join_code: 'ZZ99', grade_level: null }]))

    draw()

    expect(await screen.findByText('Untitled class')).toBeInTheDocument()
  })
})
