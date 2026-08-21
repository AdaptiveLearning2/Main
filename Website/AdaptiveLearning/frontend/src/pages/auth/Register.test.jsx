import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ signUp: vi.fn().mockResolvedValue({}) }),
}))
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import Register from './Register'

function draw() {
  render(<MemoryRouter><Register /></MemoryRouter>)
  return screen.getByPlaceholderText(/min 6 characters/i)
}

describe('the password strength meter', () => {
  it('calls the weakest password weak, rather than saying nothing', async () => {
    await userEvent.type(draw(), 'abc')

    expect(screen.getByText('Weak')).toBeInTheDocument()
  })

  it('still grades a password that passes every check', async () => {
    await userEvent.type(draw(), 'Longenough1!')

    expect(screen.getByText('Strong')).toBeInTheDocument()
  })

  it('says nothing at all before anything is typed', async () => {
    draw()
    expect(screen.queryByText(/^(Weak|Fair|Good|Strong)$/)).not.toBeInTheDocument()
  })
})

describe('the role picker', () => {
  // Matched on the sub-label, not the title: the submit button reads "Create
  // Student Account" too, so /student/ would match two elements.
  const STUDENT = { name: /practice & learn/i }
  const TEACHER = { name: /teach & analyze/i }

  it('reports which role is selected, not just colours it', async () => {
    draw()

    expect(screen.getByRole('button', STUDENT)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', TEACHER)).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(screen.getByRole('button', TEACHER))

    expect(screen.getByRole('button', TEACHER)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', STUDENT)).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('the password visibility toggle', () => {
  it('has a name that says what pressing it does', async () => {
    // Icon-only, so without this a screen reader announced it as "button".
    draw()

    await userEvent.click(screen.getByRole('button', { name: /show password/i }))

    expect(screen.getByRole('button', { name: /hide password/i })).toBeInTheDocument()
  })
})
