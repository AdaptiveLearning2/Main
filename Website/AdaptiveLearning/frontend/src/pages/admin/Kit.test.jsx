import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import AdminKit from './Kit'

const KIT = '/api/admin/kit'
const LINK = '/api/admin/kit/download-link'
const SHA = '8d953f5d18141012' + 'ab'.repeat(24)
// What GET /api/admin/kit answers (backend/main.py `admin_kit`).
const OFFERED = {
  configured: true, published: true, version: '0.2.1', file: 'AdaptiveLearningSensors-Setup-0.2.1.exe',
  sha256: SHA, size: 146618712, published_at: '2026-10-09T19:51:51+00:00',
}
const URL_ = 'https://kit-updates.example.workers.dev/v1/setup/current?exp=1791600000&sig=c1fc44f6'

const VERSIONS = '/api/admin/kit-versions'
// What GET /api/admin/kit-versions answers (backend/main.py `admin_kit_versions`).
const IN_USE = {
  retrieved: true, days: 14,
  versions: { students_by_version: { '0.2.1': 2, '0.10.0': 1, '0.9.3': 11 }, lessons_unreported: 7, students_unreported: 3 },
}

beforeEach(() => {
  resetApi()
  mockApi({
    [KIT]: OFFERED, [VERSIONS]: IN_USE,
    [`POST ${LINK}`]: { url: URL_, expires_at: '2026-10-10T02:40:00+00:00' },
  })
})

describe('the kits in use', () => {
  it('counts students by the newest version, newest first by number, and the lessons that named none', async () => {
    render(<AdminKit navigate={vi.fn()} />)
    const items = await screen.findAllByRole('listitem')
    expect(items.map(li => li.textContent)).toEqual(
      ['1 student on 0.10.0', '11 students on 0.9.3', '2 students on 0.2.1'])
    expect(screen.getByText(/Also 7 headband lessons reported no version \(3 students with no other\)/))
      .toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(VERSIONS)
  })

  it('says no lesson reported a version when none did', async () => {
    overrideApi(VERSIONS, () => ({ ...IN_USE, versions: { students_by_version: {}, lessons_unreported: 0,
                                                          students_unreported: 0 } }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText(/No headband lesson reported a kit version in the last 14 days/))
      .toBeInTheDocument()
    expect(screen.queryByText(/reported no version/)).not.toBeInTheDocument()
  })

  it('never shows unread versions as no kits', async () => {
    overrideApi(VERSIONS, () => ({ retrieved: false, days: 14, versions: null }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText('Kit versions could not be read.')).toBeInTheDocument()
    expect(screen.queryByText(/No headband lesson reported/)).not.toBeInTheDocument()
  })

  it('names a refused read as a refusal', async () => {
    overrideApi(VERSIONS, () => { throw apiError(403) })
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText("You don't have access to kit versions.")).toBeInTheDocument()
  })
})

describe('the installer on offer', () => {
  it('shows the version, size and SHA-256 the gate offers', async () => {
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText('0.2.1')).toBeInTheDocument()
    expect(screen.getByText('147 MB')).toBeInTheDocument()
    expect(screen.getByText(SHA)).toBeInTheDocument()
    expect(screen.getByText(/SHA-256 of AdaptiveLearningSensors-Setup-0.2.1.exe/)).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(KIT)
  })

  it('gives school IT the silent command for this file, with updates left to IT', async () => {
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText(
      'AdaptiveLearningSensors-Setup-0.2.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /MERGETASKS="!autoupdate"',
    )).toBeInTheDocument()
  })

  it('says nothing is published when the gate has none, and offers no button', async () => {
    overrideApi(KIT, () => ({ configured: true, published: false }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText(/No installer has been published yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Download the installer/ })).not.toBeInTheDocument()
  })

  it('says the gate is not set up rather than that nothing is published', async () => {
    overrideApi(KIT, () => ({ configured: false }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText('This server has no download gate set up.')).toBeInTheDocument()
    expect(screen.queryByText(/No installer has been published/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Download the installer/ })).not.toBeInTheDocument()
  })

  it('names a gate set up wrong as a fix to make, with no button and no retry', async () => {
    overrideApi(KIT, () => ({
      configured: true, problem_is: 'setup',
      problem: "the gate refused the link: KIT_LINK_SECRET is not the gate's LINK_SECRET",
    }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText('The download gate is set up wrong.')).toBeInTheDocument()
    expect(screen.getByText(/KIT_LINK_SECRET is not the gate's LINK_SECRET\. Fix it, then reload this page\./))
      .toBeInTheDocument()
    expect(screen.queryByText(/No installer has been published/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('names a half-finished publish as one, not as the gate being set up wrong', async () => {
    overrideApi(KIT, () => ({
      configured: true, problem_is: 'publish',
      problem: 'setup/current.json names AdaptiveLearningSensors-Setup-0.2.1.exe, which the bucket does not hold: '
        + 'publish it again with -Setup',
    }))
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText('The last publish did not finish.')).toBeInTheDocument()
    expect(screen.queryByText('The download gate is set up wrong.')).not.toBeInTheDocument()
    expect(screen.getByText(/publish it again with -Setup\. Fix it, then reload this page\./)).toBeInTheDocument()
  })

  it('never shows a failed read as nothing published', async () => {
    overrideApi(KIT, () => { throw apiError(503) })
    render(<AdminKit navigate={vi.fn()} />)
    expect(await screen.findByText("Couldn't load the kit installer just now. Try again in a moment.")).toBeInTheDocument()
    expect(screen.queryByText(/No installer has been published/)).not.toBeInTheDocument()
  })
})

describe('the download', () => {
  it('asks the backend for a link to the installer it shows, then goes to it', async () => {
    const navigate = vi.fn()
    render(<AdminKit navigate={navigate} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download the installer/ }))
    expect(apiFetch).toHaveBeenCalledWith(LINK, { method: 'POST', body: { sha256: SHA } })
    expect(navigate).toHaveBeenCalledWith(URL_)
  })

  it("says in the backend's words why no link was given when the installer changed, and goes nowhere", async () => {
    const said = 'A different installer was published after this page loaded; reload it'
    overrideApi(LINK, () => { throw apiError(409, said) }, 'POST')
    const navigate = vi.fn()
    render(<AdminKit navigate={navigate} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download the installer/ }))
    expect(await screen.findByText(said)).toBeInTheDocument()
    expect(screen.queryByText(/Make sure the backend is running/)).not.toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('names a refused link as a refusal, and goes nowhere', async () => {
    overrideApi(LINK, () => { throw apiError(403) }, 'POST')
    const navigate = vi.fn()
    render(<AdminKit navigate={navigate} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download the installer/ }))
    expect(await screen.findByText("You don't have access to a download link.")).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })
})
