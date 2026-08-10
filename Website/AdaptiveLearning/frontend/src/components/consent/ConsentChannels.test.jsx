import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const apiFetch = vi.fn()
vi.mock('../../lib/api', () => ({ apiFetch: (...a) => apiFetch(...a) }))

import ConsentChannels from './ConsentChannels'

const ALL_ON = {
  student_id: 'stu-1',
  retrieved: true,
  channels: {
    eeg: { enabled: true, revoked_at: null, revoked_by: null },
    headband_optical: { enabled: true, revoked_at: null, revoked_by: null },
    camera: { enabled: true, revoked_at: null, revoked_by: null },
  },
}

const CAMERA_OFF = {
  ...ALL_ON,
  channels: {
    ...ALL_ON.channels,
    camera: { enabled: false, revoked_at: '2026-08-01T10:00:00Z', revoked_by: 'student' },
  },
}

beforeEach(() => { apiFetch.mockReset() })

describe('reading', () => {
  it('maps the switch key to the channel the payload uses', async () => {
    // `eeg_enabled` on the write, `channels.eeg` on the read. Getting this
    // wrong renders every switch as off and looks exactly like a student who
    // withdrew everything -- silent, and the worst possible direction to be
    // wrong in on a consent screen.
    apiFetch.mockResolvedValue(ALL_ON)

    render(<ConsentChannels studentId="stu-1" role="student" />)

    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(3))
    for (const s of screen.getAllByRole('switch')) {
      expect(s).toHaveAttribute('aria-checked', 'true')
    }
  })

  it('says when a channel was turned off, never just "no data"', async () => {
    apiFetch.mockResolvedValue(CAMERA_OFF)

    render(<ConsentChannels studentId="stu-1" role="student" />)

    expect(await screen.findByText(/Not recorded — turned off on/)).toBeInTheDocument()
  })

  it('does not render a failed read as everything being off', async () => {
    // `retrieved: false` is "we could not find out", which is not "the student
    // withdrew". Telling a parent the second when the first is true is the
    // three-state failure the reporting rules exist to stop.
    apiFetch.mockResolvedValue({ ...ALL_ON, retrieved: false })

    render(<ConsentChannels studentId="stu-1" role="parent" />)

    expect(await screen.findByText(/Could not load these settings/)).toBeInTheDocument()
  })
})

describe('the student can only switch off', () => {
  it('confirms before withdrawing, and says a parent can restore it', async () => {
    apiFetch.mockResolvedValue(ALL_ON)
    render(<ConsentChannels studentId="stu-1" role="student" />)
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(3))

    await userEvent.click(screen.getByRole('switch', { name: 'Camera' }))

    expect(screen.getByText(/stays off/)).toBeInTheDocument()
    expect(screen.getByText(/a parent has to switch it back on/)).toBeInTheDocument()
    // Nothing written until they confirm.
    expect(apiFetch).toHaveBeenCalledTimes(1)
  })

  it('writes only the channel that changed', async () => {
    apiFetch.mockResolvedValueOnce(ALL_ON).mockResolvedValueOnce(CAMERA_OFF)
    render(<ConsentChannels studentId="stu-1" role="student" />)
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(3))

    await userEvent.click(screen.getByRole('switch', { name: 'Camera' }))
    await userEvent.click(screen.getByRole('button', { name: /Turn it off/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    expect(apiFetch.mock.calls[1][1]).toMatchObject({
      method: 'PUT', body: { camera_enabled: false },
    })
  })

  it('cannot switch a withdrawn channel back on', async () => {
    // The backend enforces this; the UI states it. A switch that vanishes when
    // you turn it off looks like a bug, so it stays visible and disabled.
    apiFetch.mockResolvedValue(CAMERA_OFF)
    render(<ConsentChannels studentId="stu-1" role="student" />)

    const camera = await screen.findByRole('switch', { name: 'Camera' })
    expect(camera).toBeDisabled()
    expect(screen.getByText(/A parent can turn this back on/)).toBeInTheDocument()
  })
})

describe('the parent can switch on', () => {
  it('re-enables without a confirmation step', async () => {
    // Reversible by the same parent on the same screen, so there is nothing to
    // warn about. The student's withdrawal is the irreversible one.
    apiFetch.mockResolvedValueOnce(CAMERA_OFF).mockResolvedValueOnce(ALL_ON)
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    const camera = await screen.findByRole('switch', { name: 'Camera' })
    expect(camera).not.toBeDisabled()
    await userEvent.click(camera)

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    expect(apiFetch.mock.calls[1][1].body).toEqual({ camera_enabled: true })
  })

  it('reloads on a 409 rather than retrying', async () => {
    // The decision moved under us -- a student withdrew while the parent was
    // re-enabling. Retrying would record against a refusal.
    const conflict = Object.assign(new Error('Consent changed'), { status: 409 })
    apiFetch.mockResolvedValueOnce(CAMERA_OFF)
             .mockRejectedValueOnce(conflict)
             .mockResolvedValueOnce(CAMERA_OFF)
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await userEvent.click(await screen.findByRole('switch', { name: 'Camera' }))

    expect(await screen.findByText(/changed somewhere else/)).toBeInTheDocument()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(3))
    // The third call is a re-read, not a second write.
    expect(apiFetch.mock.calls[2][1]).toBeUndefined()
  })
})

describe('copy', () => {
  it('says recorded, and never explains what the control does not do', async () => {
    // The old control was a display filter, and its disclaimer sentence is what
    // made it confusing. Needing one is the signal the control is wrong.
    apiFetch.mockResolvedValue(ALL_ON)
    const { container } = render(<ConsentChannels studentId="stu-1" role="student" />)
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(3))

    const text = container.textContent
    expect(text).not.toMatch(/does not switch a camera on or off/i)
    expect(text).not.toMatch(/facial recognition/i)
    expect(text).toMatch(/saved/)
  })
})
