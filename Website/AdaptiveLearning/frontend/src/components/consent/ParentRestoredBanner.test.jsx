import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const apiFetch = vi.fn()
vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))

import ParentRestoredBanner from './ParentRestoredBanner'

beforeEach(() => { apiFetch.mockReset() })

describe('ParentRestoredBanner', () => {
  it('says nothing when nothing was restored', async () => {
    apiFetch.mockResolvedValue({ needs_student_ack: false })

    const { container } = render(<ParentRestoredBanner studentId="stu-1" />)

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('tells the student when a parent turned a sensor back on', async () => {
    // Only a parent may restore, so a channel can start recording again
    // without the student doing anything -- finding out from data reappearing
    // would be a surprise, not consent.
    apiFetch.mockResolvedValue({ needs_student_ack: true })

    render(<ParentRestoredBanner studentId="stu-1" />)

    expect(await screen.findByText(/turned a sensor back on/i)).toBeInTheDocument()
  })

  it('does not claim anything happened when the read fails', async () => {
    // A failed consent read answers with defaults, so it must not claim a
    // change occurred.
    apiFetch.mockRejectedValue(new Error('network down'))

    const { container } = render(<ParentRestoredBanner studentId="stu-1" />)

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('clears the flag when acknowledged', async () => {
    apiFetch.mockResolvedValueOnce({ needs_student_ack: true })
             .mockResolvedValueOnce({ ok: true })

    render(<ParentRestoredBanner studentId="stu-1" />)
    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    expect(apiFetch).toHaveBeenLastCalledWith('/api/consent/ack', { method: 'POST' })
    await waitFor(() => expect(screen.queryByText(/turned a sensor back on/i)).not.toBeInTheDocument())
  })

  it('stays up if the acknowledgement does not land', async () => {
    // The student has not actually been told yet.
    apiFetch.mockResolvedValueOnce({ needs_student_ack: true })
             .mockRejectedValueOnce(new Error('offline'))

    render(<ParentRestoredBanner studentId="stu-1" />)
    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Got it/ })).toBeEnabled())
    expect(screen.getByText(/turned a sensor back on/i)).toBeInTheDocument()
  })
})
