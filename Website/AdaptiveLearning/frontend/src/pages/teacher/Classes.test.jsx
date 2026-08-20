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
    // A blank name must not crash the whole list on `''[0].toUpperCase()`.
    overrideApi('/api/classes', () => ([{ ...CLASS, id: 'c-2', name: '' }, CLASS]))

    draw()

    // Confirms the list rendered rather than being replaced by an error screen.
    expect(await screen.findByText('Year 7 Maths')).toBeInTheDocument()
    expect(screen.getByText('Untitled class')).toBeInTheDocument()
  })

  it('survives a class with no name field at all', async () => {
    overrideApi('/api/classes', () => ([{ id: 'c-3', join_code: 'ZZ99', grade_level: null }]))

    draw()

    expect(await screen.findByText('Untitled class')).toBeInTheDocument()
  })
})
