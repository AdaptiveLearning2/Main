import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const apiFetch = vi.fn()
vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))

import ParentLinkedBanner from './ParentLinkedBanner'

const ONE = {
  retrieved: true,
  links: [{ id: 'l-1', parent_name: 'Rae', linked_at: '2026-08-14T09:00:00Z' }],
}

beforeEach(() => { apiFetch.mockReset() })

describe('ParentLinkedBanner', () => {
  it('says nothing when no new link is waiting', async () => {
    apiFetch.mockResolvedValue({ retrieved: true, links: [] })

    const { container } = render(<ParentLinkedBanner studentId="stu-1" />)

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('tells the student who linked, and what that lets them do', async () => {
    // A linked parent may read reports and re-enable a sensor the child turned off.
    apiFetch.mockResolvedValue(ONE)

    render(<ParentLinkedBanner studentId="stu-1" />)

    expect(await screen.findByText(/Rae linked to your account/i)).toBeInTheDocument()
    // Both powers are named, not just "a parent is linked".
    expect(screen.getByText(/progress reports/i)).toBeInTheDocument()
    expect(screen.getByText(/turn a sensor back on/i)).toBeInTheDocument()
  })

  it('counts them when more than one parent is waiting', async () => {
    // Per link, not per student.
    apiFetch.mockResolvedValue({
      retrieved: true,
      links: [
        { id: 'l-1', parent_name: 'Rae',  linked_at: '2026-08-14T09:00:00Z' },
        { id: 'l-2', parent_name: 'Sam',  linked_at: '2026-08-10T09:00:00Z' },
      ],
    })

    render(<ParentLinkedBanner studentId="stu-1" />)

    expect(await screen.findByText(/2 parents linked to your account/i)).toBeInTheDocument()
  })

  it('does not claim a link exists when the read failed', async () => {
    // The endpoint fails open to an empty list; a failure claims nothing.
    apiFetch.mockResolvedValue({ retrieved: false, links: [] })

    const { container } = render(<ParentLinkedBanner studentId="stu-1" />)

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says nothing when the request itself throws', async () => {
    apiFetch.mockRejectedValue(new Error('network down'))

    const { container } = render(<ParentLinkedBanner studentId="stu-1" />)

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('clears when acknowledged', async () => {
    apiFetch.mockResolvedValueOnce(ONE).mockResolvedValueOnce({ ok: true, acknowledged: 1 })

    render(<ParentLinkedBanner studentId="stu-1" />)
    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    expect(apiFetch).toHaveBeenLastCalledWith('/api/student/parent-links/ack', { method: 'POST' })
    await waitFor(() =>
      expect(screen.queryByText(/linked to your account/i)).not.toBeInTheDocument())
  })

  it('stays up if the acknowledgement does not land', async () => {
    // The student has not actually been told yet.
    apiFetch.mockResolvedValueOnce(ONE).mockRejectedValueOnce(new Error('offline'))

    render(<ParentLinkedBanner studentId="stu-1" />)
    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Got it/ })).toBeEnabled())
    expect(screen.getByText(/linked to your account/i)).toBeInTheDocument()
  })

  it('does not read anything before it knows who the student is', async () => {
    render(<ParentLinkedBanner studentId={undefined} />)
    expect(apiFetch).not.toHaveBeenCalled()
  })
})
