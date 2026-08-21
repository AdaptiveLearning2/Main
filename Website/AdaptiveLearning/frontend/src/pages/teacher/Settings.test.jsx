import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
vi.mock('../../lib/supabase', async () => await import('../../test/mocks/supabase'))

const toastError = vi.fn()
const toastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: { error: (...a) => toastError(...a), success: (...a) => toastSuccess(...a) },
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 't-1', email: 'teacher@example.com' }, signOut: vi.fn() }),
}))
vi.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ dark: false, toggleTheme: vi.fn() }),
}))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import { authFns, resetSupabaseMock } from '../../test/mocks/supabase'
import TeacherSettings from './Settings'

const draw = () => render(<MemoryRouter><TeacherSettings /></MemoryRouter>)

beforeEach(() => {
  resetApi()
  resetSupabaseMock()
  toastError.mockReset()
  toastSuccess.mockReset()
  mockApi({
    '/api/profile/me': () => ({ display_name: 'Ms Patel' }),
    'PUT /api/profile/me': () => ({ display_name: 'Ms Patel' }),
  })
})

describe('the display name', () => {
  it('shows what is stored, not a guess from the email', async () => {
    draw()
    expect(await screen.findByDisplayValue('Ms Patel')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('teacher')).not.toBeInTheDocument()
  })

  it('actually saves it', async () => {
    draw()
    // Waits for the value, not just presence: the field is disabled until the
    // profile loads, and userEvent.clear() throws on a disabled element.
    await screen.findByDisplayValue('Ms Patel')
    const field = screen.getByLabelText(/display name/i)
    await userEvent.clear(field)
    await userEvent.type(field, 'Ms Khan')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/api/profile/me', expect.objectContaining({
        method: 'PUT',
        body: { display_name: 'Ms Khan' },
      }))
    })
    expect(toastSuccess).toHaveBeenCalled()
  })

  it('says so when the save fails, rather than claiming success', async () => {
    overrideApi('/api/profile/me', () => { throw apiError(500, 'down') }, 'PUT')

    draw()
    await screen.findByDisplayValue('Ms Patel')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
})

describe('changing the password', () => {
  async function fill(values) {
    draw()
    await screen.findByDisplayValue('Ms Patel')
    await userEvent.click(screen.getByRole('button', { name: /^security$/i }))
    for (const [label, value] of Object.entries(values)) {
      await userEvent.type(screen.getByLabelText(new RegExp(label, 'i')), value)
    }
  }

  it('checks the current password before changing anything', async () => {
    // Supabase's updateUser doesn't ask for the old password itself, so the
    // form must verify it -- otherwise a shared school machine left signed in
    // gives anyone at the desk a way to change the password.
    authFns.signInWithPassword.mockResolvedValue({ error: new Error('bad creds') })

    await fill({ 'current password': 'wrong', '^new password': 'newpass123', 'confirm new': 'newpass123' })
    await userEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('That current password is not right.'))
    expect(authFns.updateUser).not.toHaveBeenCalled()
  })

  it('updates it once the current one checks out', async () => {
    authFns.signInWithPassword.mockResolvedValue({ error: null })
    authFns.updateUser.mockResolvedValue({ error: null })

    await fill({ 'current password': 'right', '^new password': 'newpass123', 'confirm new': 'newpass123' })
    await userEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() =>
      expect(authFns.updateUser).toHaveBeenCalledWith({ password: 'newpass123' }))
    expect(toastSuccess).toHaveBeenCalledWith('Password updated.')
  })

  it('refuses a mismatch without touching the account', async () => {
    await fill({ 'current password': 'right', '^new password': 'newpass123', 'confirm new': 'different' })
    await userEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('The new passwords do not match.'))
    expect(authFns.signInWithPassword).not.toHaveBeenCalled()
    expect(authFns.updateUser).not.toHaveBeenCalled()
  })
})

describe('the tabs', () => {
  it('does not offer notification switches with nothing behind them', async () => {
    // There's no push infrastructure, so notification toggles would tell a teacher something is on when it isn't.
    draw()
    await screen.findByDisplayValue('Ms Patel')

    expect(screen.queryByRole('button', { name: /notifications/i })).not.toBeInTheDocument()
  })
})
