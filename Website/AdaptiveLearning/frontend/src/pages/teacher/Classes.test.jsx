import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
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

    expect(await screen.findByText('Year 7 Maths')).toBeInTheDocument()
    expect(screen.getByText('Untitled class')).toBeInTheDocument()
  })

  it('survives a class with no name field at all', async () => {
    overrideApi('/api/classes', () => ([{ id: 'c-3', join_code: 'ZZ99', grade_level: null }]))

    draw()

    expect(await screen.findByText('Untitled class')).toBeInTheDocument()
  })
})

describe('editing a grade', () => {
  it('opens a class with no grade on "Not set", and saving that untouched writes nothing', async () => {
    overrideApi('/api/classes', () => ([{ ...CLASS, grade_level: null }]))
    draw()
    await screen.findByText('Grade not set')

    // The pencil and save buttons carry icons only; they are the ones beside the grade.
    const badge = screen.getByText('Grade not set')
    await userEvent.click(within(badge).getByRole('button'))
    const picker = screen.getByRole('combobox')
    expect(picker).toHaveValue('')
    expect(picker).toHaveDisplayValue('Not set')
    await userEvent.click(picker.nextElementSibling)

    expect(apiFetch.mock.calls.some(([, opts]) => opts?.method === 'PUT')).toBe(false)
    expect(await screen.findByText('Grade not set')).toBeInTheDocument()
  })

  it('writes a grade picked from "Not set"', async () => {
    overrideApi('/api/classes', () => ([{ ...CLASS, grade_level: null }]))
    overrideApi('/api/classes/c-1', () => ({ ...CLASS, grade_level: '2nd Grade' }), 'PUT')
    draw()
    await userEvent.click(within(await screen.findByText('Grade not set')).getByRole('button'))

    await userEvent.selectOptions(screen.getByRole('combobox'), '2nd Grade')
    await userEvent.click(screen.getByRole('combobox').nextElementSibling)

    expect(apiFetch).toHaveBeenCalledWith('/api/classes/c-1',
      { method: 'PUT', body: { grade_level: '2nd Grade' } })
  })
})
