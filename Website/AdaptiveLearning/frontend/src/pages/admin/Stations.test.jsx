import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import { STATIONS_PATH, buildStations, station } from '../../test/fixtures/adminLifecycle'
import AdminStations, { StationHolds } from './Stations'

beforeEach(() => {
  resetApi()
  mockApi({ [STATIONS_PATH]: buildStations() })
})

const page = () => render(<MemoryRouter><AdminStations /></MemoryRouter>)
const card = id => screen.getByText(id).closest('li')

describe('the stations page', () => {
  it('shows who holds each station and how recently their page polled', async () => {
    page()
    await screen.findByText('station1')
    expect(within(card('station1')).getByText(/Held by Ada, page last seen 4 s ago/)).toBeInTheDocument()
    expect(within(card('station1')).getByText('1 refused today')).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(STATIONS_PATH)
  })

  it('marks the poller still running for a page that went silent, and only that one', async () => {
    page()
    await screen.findByText('station2')
    expect(within(card('station2')).getByText(/page silent, poller still running/)).toBeInTheDocument()
    expect(within(card('station2')).getByText(/idle past 120 s/)).toBeInTheDocument()
    expect(within(card('station1')).queryByText(/page silent/)).not.toBeInTheDocument()
  })

  it('says how a released station’s last lesson today ended, in words', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({
      stations: [station(), station({ device_id: 'station3', pairing: null, pollers: [], refused_today: 0,
                                      last_ended: { close_reason: 'sweep', ended_at: '2026-10-08T16:40:00+00:00' } })],
    }))
    page()
    await screen.findByText('station3')
    expect(within(card('station3')).getByText('Not held.')).toBeInTheDocument()
    expect(within(card('station3')).getByText(/Last lesson here today ended .*: Closed by the sweep as abandoned/))
      .toBeInTheDocument()
    expect(within(card('station1')).queryByText(/Last lesson here/)).not.toBeInTheDocument()
  })

  it('gives the end time in the school’s timezone, named, whatever the browser’s', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({
      timezone: 'Asia/Tokyo',
      stations: [station({ last_ended: { close_reason: 'finish', ended_at: '2026-10-08T16:40:00+00:00' } })],
    }))
    page()
    const line = await screen.findByText(/Last lesson here today ended/)
    // Built with the machine's own locale, so only the zone is under test, not 12- or 24-hour style.
    const tokyo = new Date('2026-10-08T16:40:00+00:00').toLocaleTimeString([],
      { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo', timeZoneName: 'short' })
    expect(line.textContent).toContain(`ended at ${tokyo}:`)
  })

  it('falls back to the browser’s zone, still named, for a zone it does not recognise', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({
      timezone: 'Not/AZone',
      stations: [station({ last_ended: { close_reason: 'finish', ended_at: '2026-10-08T16:40:00+00:00' } })],
    }))
    page()
    const line = await screen.findByText(/Last lesson here today ended/)
    const local = new Date('2026-10-08T16:40:00+00:00').toLocaleTimeString([],
      { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
    expect(line.textContent).toContain(`ended at ${local}:`)
  })

  it('names a null reason as unrecorded, never as a guess', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({
      stations: [station({ last_ended: { close_reason: null, ended_at: '2026-10-08T16:40:00+00:00' } })],
    }))
    page()
    expect(await screen.findByText(/Last lesson here today ended .*: Ended before reasons were recorded/))
      .toBeInTheDocument()
  })

  it('says an unread last lesson may hide a released station', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({ last_ended_retrieved: false }))
    page()
    expect(await screen.findByText(/last lesson ended could not be read/)).toBeInTheDocument()
  })

  it('says nothing about last lessons when they were read', async () => {
    page()
    await screen.findByText('station1')
    expect(screen.queryByText(/last lesson ended could not be read/)).not.toBeInTheDocument()
  })

  it('says a name could not be read rather than that it is unset', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({
      names_retrieved: false,
      stations: [station({ pairing: { user_id: 'u-ada', name: null, session_id: 's', seen_seconds_ago: 4, idle: false },
                           pollers: [] })],
    }))
    page()
    expect(await screen.findByText(/Held by Name unavailable/)).toBeInTheDocument()
  })

  it('says no station is held when the read found none', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({ stations: [] }))
    page()
    expect(await screen.findByText('No station is held right now.')).toBeInTheDocument()
  })

  it('never shows an unread pairing table as no stations held', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({ retrieved: false, stations: [] }))
    page()
    expect(await screen.findByText(/Station pairings could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No station is held/)).not.toBeInTheDocument()
  })

  it('explains an empty list under push ingestion', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({ ingest_mode: 'push', stations: [] }))
    page()
    expect(await screen.findByText(/uses push ingestion/)).toBeInTheDocument()
  })

  it('names a refusal as one', async () => {
    overrideApi(STATIONS_PATH, () => { throw apiError(403) })
    page()
    expect(await screen.findByText("You don't have access to stations.")).toBeInTheDocument()
  })
})

describe("the Overview's card", () => {
  it('counts stations held, silent pages and refusals', async () => {
    render(<MemoryRouter><StationHolds /></MemoryRouter>)
    const held = await screen.findByText('Stations held')
    expect(held.previousSibling).toHaveTextContent('2')
    expect(screen.getByText('Pollers with a silent page').previousSibling).toHaveTextContent('1')
    expect(screen.getByText('Refused today').previousSibling).toHaveTextContent('1')
  })

  it('shows a dash, not a zero, for refusals it could not read', async () => {
    overrideApi(STATIONS_PATH, () => buildStations({ refusals_retrieved: false }))
    render(<MemoryRouter><StationHolds /></MemoryRouter>)
    expect((await screen.findByText('Refused today')).previousSibling).toHaveTextContent('—')
  })
})
