import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const apiFetch = vi.fn()
vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))

import ChildWithdrewBanner from './ChildWithdrewBanner'

const draw = () => render(<MemoryRouter><ChildWithdrewBanner /></MemoryRouter>)

const ONE = {
  retrieved: true,
  notices: [{
    child_id: 'kid-1', child_name: 'Ada',
    channels: [{ channel: 'camera', label: 'the camera', at: '2026-08-12T09:00:00Z' }],
    through: '2026-08-12T09:00:00Z',
  }],
}

beforeEach(() => { apiFetch.mockReset() })

describe('ChildWithdrewBanner', () => {
  it('says nothing when no child has withdrawn anything', async () => {
    apiFetch.mockResolvedValue({ retrieved: true, notices: [] })

    const { container } = draw()

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('names the child, the sensor and the day', async () => {
    apiFetch.mockResolvedValue(ONE)
    draw()

    expect(await screen.findByText(/Ada switched a sensor off/i)).toBeInTheDocument()
    expect(screen.getByText(/turned off the camera/i)).toBeInTheDocument()
  })

  it('offers no way to override the decision from here', async () => {
    // A student's withdrawal stands. A button here would make overriding a
    // child's decision the default response to hearing about it.
    apiFetch.mockResolvedValue(ONE)
    draw()

    await screen.findByText(/Ada switched a sensor off/i)
    expect(screen.queryByRole('button', { name: /turn .*(back on|on again)/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /settings/i })).toBeInTheDocument()
  })

  it('says what is and is not affected', async () => {
    // Withdrawal stops future recording but keeps what's already stored --
    // "a sensor is off" alone reads as erasure otherwise.
    apiFetch.mockResolvedValue(ONE)
    draw()

    expect(await screen.findByText(/what was recorded before is unchanged/i)).toBeInTheDocument()
  })

  it('lists every channel across every child', async () => {
    apiFetch.mockResolvedValue({
      retrieved: true,
      notices: [
        { child_id: 'kid-1', child_name: 'Ada', channels: [
          { channel: 'camera', label: 'the camera', at: '2026-08-12T09:00:00Z' },
          { channel: 'eeg', label: 'the headband', at: '2026-08-11T09:00:00Z' }] },
        { child_id: 'kid-2', child_name: 'Basil', channels: [
          { channel: 'eeg', label: 'the headband', at: '2026-08-10T09:00:00Z' }] },
      ],
    })
    draw()

    expect(await screen.findByText(/Some sensors were switched off/i)).toBeInTheDocument()
    expect(screen.getByText(/Ada turned off the camera/i)).toBeInTheDocument()
    expect(screen.getByText(/Basil turned off the headband/i)).toBeInTheDocument()
  })

  it('does not claim a withdrawal when the read failed', async () => {
    apiFetch.mockResolvedValue({ retrieved: false, notices: [] })

    const { container } = draw()

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says nothing when the request throws', async () => {
    apiFetch.mockRejectedValue(new Error('offline'))

    const { container } = draw()

    await waitFor(() => expect(apiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('clears when acknowledged', async () => {
    apiFetch.mockResolvedValueOnce(ONE).mockResolvedValueOnce({ ok: true })
    draw()

    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    // Sends back the server's watermark per child -- acking with `now()`
    // instead would silently mark seen any withdrawal that landed in between.
    expect(apiFetch).toHaveBeenLastCalledWith('/api/parent/consent-notices/ack',
      { method: 'POST', body: { through: { 'kid-1': '2026-08-12T09:00:00Z' } } })
    await waitFor(() =>
      expect(screen.queryByText(/switched a sensor off/i)).not.toBeInTheDocument())
  })

  it('stays up if the acknowledgement does not land', async () => {
    apiFetch.mockResolvedValueOnce(ONE).mockRejectedValueOnce(new Error('offline'))
    draw()

    await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Got it/ })).toBeEnabled())
    expect(screen.getByText(/Ada switched a sensor off/i)).toBeInTheDocument()
  })
})

it('sends a watermark for every child it displayed', async () => {
  apiFetch.mockResolvedValueOnce({
    retrieved: true,
    notices: [
      { child_id: 'kid-1', child_name: 'Ada', through: '2026-08-12T09:00:00Z',
        channels: [{ channel: 'camera', label: 'the camera', at: '2026-08-12T09:00:00Z' }] },
      { child_id: 'kid-2', child_name: 'Basil', through: '2026-08-10T09:00:00Z',
        channels: [{ channel: 'eeg', label: 'the headband', at: '2026-08-10T09:00:00Z' }] },
    ],
  }).mockResolvedValueOnce({ ok: true })

  draw()
  await userEvent.click(await screen.findByRole('button', { name: /Got it/ }))

  // Per child, not one stamp for the family -- acking both at the later time
  // would swallow anything that landed for Basil in between.
  expect(apiFetch).toHaveBeenLastCalledWith('/api/parent/consent-notices/ack',
    { method: 'POST', body: { through: {
      'kid-1': '2026-08-12T09:00:00Z', 'kid-2': '2026-08-10T09:00:00Z' } } })
})
