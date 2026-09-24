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

// A real failed read: consent fails closed, so every channel off and no date.
const READ_FAILED = {
  student_id: 'stu-1',
  retrieved: false,
  channels: {
    eeg: { enabled: false, revoked_at: null, revoked_by: null },
    headband_optical: { enabled: false, revoked_at: null, revoked_by: null },
    camera: { enabled: false, revoked_at: null, revoked_by: null },
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
    // `eeg_enabled` on the write, `channels.eeg` on the read.
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
    apiFetch.mockResolvedValue(READ_FAILED)

    render(<ConsentChannels studentId="stu-1" role="parent" />)

    expect(await screen.findByText(/Could not load these settings/)).toBeInTheDocument()
    // The banner alone is not enough beside three off switches.
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
  })

  it('does not tell a student a parent must restore what nobody switched off', async () => {
    apiFetch.mockResolvedValue(READ_FAILED)

    render(<ConsentChannels studentId="stu-1" role="student" />)

    expect(await screen.findByText(/Could not load these settings/)).toBeInTheDocument()
    expect(screen.queryByText(/A parent can turn this back on for you/)).not.toBeInTheDocument()
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
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
    // Nothing written yet.
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
    // The backend enforces this; the switch stays visible and disabled.
    apiFetch.mockResolvedValue(CAMERA_OFF)
    render(<ConsentChannels studentId="stu-1" role="student" />)

    const camera = await screen.findByRole('switch', { name: 'Camera' })
    expect(camera).toBeDisabled()
    expect(screen.getByText(/A parent can turn this back on/)).toBeInTheDocument()
  })
})

describe('the parent can switch on', () => {
  it('re-enables without a confirmation step', async () => {
    // Reversible by the same parent; only the student's withdrawal is not.
    apiFetch.mockResolvedValueOnce(CAMERA_OFF).mockResolvedValueOnce(ALL_ON)
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    const camera = await screen.findByRole('switch', { name: 'Camera' })
    expect(camera).not.toBeDisabled()
    await userEvent.click(camera)

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    expect(apiFetch.mock.calls[1][1].body).toEqual({ camera_enabled: true })
  })

  it('reloads on a 409 rather than retrying', async () => {
    // Retrying would record against a refusal.
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

  it('does not promise nothing changed when the reload after a conflict fails', async () => {
    // After a 409, "Nothing has been changed" is false both ways.
    const conflict = Object.assign(new Error('Consent changed'), { status: 409 })
    apiFetch.mockResolvedValueOnce(CAMERA_OFF)
             .mockRejectedValueOnce(conflict)
             .mockResolvedValueOnce(READ_FAILED)
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await userEvent.click(await screen.findByRole('switch', { name: 'Camera' }))

    expect(await screen.findByText(/your change was not applied/)).toBeInTheDocument()
    expect(screen.queryByText(/Nothing has been changed/)).not.toBeInTheDocument()
    // The 409 says the on-screen state is superseded.
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
  })

  it('says the same when the reload after a conflict throws', async () => {
    // A thrown read and `retrieved: false` mean the same thing here.
    const conflict = Object.assign(new Error('Consent changed'), { status: 409 })
    apiFetch.mockResolvedValueOnce(CAMERA_OFF)
             .mockRejectedValueOnce(conflict)
             .mockRejectedValueOnce(new Error('Network down'))
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await userEvent.click(await screen.findByRole('switch', { name: 'Camera' }))

    expect(await screen.findByText(/your change was not applied/)).toBeInTheDocument()
    expect(screen.queryByText(/Network down/)).not.toBeInTheDocument()
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
  })
})

describe('copy', () => {
  it('says recorded, and never explains what the control does not do', async () => {
    apiFetch.mockResolvedValue(ALL_ON)
    const { container } = render(<ConsentChannels studentId="stu-1" role="student" />)
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(3))

    const text = container.textContent
    expect(text).not.toMatch(/does not switch a camera on or off/i)
    expect(text).not.toMatch(/facial recognition/i)
    expect(text).toMatch(/saved/)
  })
})

// ── erasure ──────────────────────────────────────────────────────────────
// Separate from consent: only a linked parent may erase, and nothing undoes it.

const ERASED = {
  ...ALL_ON,
  channels: {
    ...ALL_ON.channels,
    camera: { enabled: true, revoked_at: null, revoked_by: null,
              erased_at: '2026-08-11T09:00:00Z' },
  },
}

function eraseOk(extra = {}) {
  apiFetch.mockImplementation((url, opts) => {
    if (url.endsWith('/erase')) return Promise.resolve({ channel: 'camera', charts_failed: 0, ...extra })
    return Promise.resolve(opts?.method === 'PUT' ? ALL_ON : ERASED)
  })
}

describe('erasing stored readings', () => {
  it('is not offered to the student', async () => {
    // The backend refuses students outright.
    apiFetch.mockResolvedValue(ERASED)
    render(<ConsentChannels studentId="stu-1" role="student" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    expect(screen.queryByText(/erase what this recorded/i)).not.toBeInTheDocument()
  })

  it('tells the student their readings were erased', async () => {
    apiFetch.mockResolvedValue(ERASED)
    render(<ConsentChannels studentId="stu-1" role="student" />)

    await waitFor(() =>
      expect(screen.getByText(/readings recorded before .* were erased/i))
        .toBeInTheDocument())
  })

  it('reports an erasure even though the channel is still on', async () => {
    // Erasure is about stored history, not the current decision.
    apiFetch.mockResolvedValue(ERASED)
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() =>
      expect(screen.getByText(/were erased/i)).toBeInTheDocument())
    expect(screen.getAllByRole('switch')[2]).toHaveAttribute('aria-checked', 'true')
  })

  it('will not erase until the parent acknowledges it cannot be undone', async () => {
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])

    const button = screen.getByRole('button', { name: /erase them/i })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(apiFetch.mock.calls.some(c => String(c[0]).endsWith('/erase'))).toBe(false)

    await user.click(screen.getByRole('checkbox'))
    expect(button).not.toBeDisabled()
  })

  it('sends the channel name the endpoint takes, not the switch key', async () => {
    // `camera_enabled` is the flag; `camera` is the channel. The flag 422s.
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /erase them/i }))

    await waitFor(() => {
      const call = apiFetch.mock.calls.find(c => String(c[0]).endsWith('/erase'))
      expect(call).toBeTruthy()
      expect(call[1].body).toEqual({ channel: 'camera', confirm: true })
    })
  })

  it('does not carry the acknowledgement over to another channel', async () => {
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    // Straight to another channel, not via Cancel, which also clears the box.
    await user.click(screen.getAllByText(/erase what this recorded/i)[0])
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: /erase them/i })).toBeDisabled()
  })

  it('says so when an archived chart could not be removed', async () => {
    // Storage removal runs after the rows are gone, so it alone can stay incomplete.
    const user = userEvent.setup()
    eraseOk({ charts_failed: 2 })
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /erase them/i }))

    await waitFor(() =>
      expect(screen.getByText(/some archived charts could not be removed/i))
        .toBeInTheDocument())
  })

  it('warns that erasing does not stop the sensor still recording', async () => {
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])

    expect(screen.getByText(/new readings will be recorded from now on/i))
      .toBeInTheDocument()
  })
})

describe('the erasure result banner', () => {
  it('does not use the failure colour for a successful erasure', async () => {
    // Rose is reserved for the destructive action itself.
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /erase them/i }))

    const note = await screen.findByText('Erased.')
    expect(note.className).not.toMatch(/rose/)
    expect(note.className).toMatch(/emerald/)
  })

  it('keeps the failure colour when a chart could not be removed', async () => {
    const user = userEvent.setup()
    eraseOk({ charts_failed: 1 })
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /erase them/i }))

    const note = await screen.findByText(/some archived charts could not be removed/i)
    expect(note.className).toMatch(/rose/)
  })

  it('clears the note when the parent does something else', async () => {
    const user = userEvent.setup()
    eraseOk()
    render(<ConsentChannels studentId="stu-1" role="parent" />)

    await waitFor(() => expect(screen.getByText('Camera')).toBeInTheDocument())
    await user.click(screen.getAllByText(/erase what this recorded/i)[2])
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /erase them/i }))
    await screen.findByText('Erased.')

    await user.click(screen.getAllByRole('switch')[0])

    await waitFor(() => expect(screen.queryByText('Erased.')).not.toBeInTheDocument())
  })
})
