import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import {
  GENERATION_PATH, REFUSALS_PATH, buildGeneration, buildRefusals,
} from '../../test/fixtures/opsCounters'
import { QUALITY_PATH, buildSignalQuality } from '../../test/fixtures/adminTotals'
import { ADAPTIVE_PATH, buildAdaptive } from '../../test/fixtures/adminAdaptive'
import AdminEngine, { RefusalSummary } from './Engine'

beforeEach(() => {
  resetApi()
  mockApi({ [GENERATION_PATH]: buildGeneration(), [REFUSALS_PATH]: buildRefusals(),
            [QUALITY_PATH]: buildSignalQuality(), [ADAPTIVE_PATH]: buildAdaptive() })
})

const page = () => render(<MemoryRouter><AdminEngine /></MemoryRouter>)
const section = name => screen.getByRole('heading', { name }).closest('section')

describe('question generation', () => {
  it('asks for the last 24 hours and shows the totals and the ceiling it was given', async () => {
    page()
    expect(await screen.findByText('26')).toBeInTheDocument()   // 5 inline + 15 queued + 6 practice

    const gen = section('Question generation')
    expect(within(gen).getByText(/6 practice/)).toBeInTheDocument()
    expect(within(gen).getByText('40')).toBeInTheDocument()
    expect(within(gen).getByText('4')).toBeInTheDocument()
    expect(within(gen).getByText('44 / 2500')).toBeInTheDocument()
    expect(within(gen).getByText('this server process')).toBeInTheDocument()
    expect(within(gen).getByText('$0.14')).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(GENERATION_PATH)
  })

  it('names each failure in words, not as a counter key', async () => {
    page()
    expect(await screen.findByText(/daily call ceiling reached/)).toBeInTheDocument()
    expect(screen.getByText(/error: ValueError/)).toBeInTheDocument()
    expect(screen.queryByText(/claude:unavailable/)).not.toBeInTheDocument()
  })

  it('counts a rejected reply as a question not made, though every model call was ok', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({
      outcomes: { 'claude:ok': 40 }, questions: { 'served:inline': 5, 'generation_failed:ValueError': 2 },
    }))
    page()
    const gen = section('Question generation')
    expect(await within(gen).findByText(/not made: ValueError/)).toHaveTextContent('2 × not made: ValueError')
    const tile = within(gen).getByText('Questions not made').closest('div')
    expect(within(tile).getByText('2')).toBeInTheDocument()
    expect(within(gen).getByText('Model calls failed').closest('div')).toHaveTextContent('0')
  })

  it('says a quiet day was read, rather than showing zeros alone', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({ outcomes: {}, questions: {}, waits: {} }))
    page()
    expect(await screen.findByText(/No model calls recorded in the last 24 hours/)).toBeInTheDocument()
  })

  it('never shows an unread store as a quiet day', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({ retrieved: false, outcomes: {}, questions: {}, waits: {} }))
    page()
    expect(await screen.findByText(/Stored generation counts could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No model calls recorded/)).not.toBeInTheDocument()
  })

  it('shows no cost when the provider bills nothing', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({ provider: 'ollama', model: null }))
    page()
    await screen.findByText('26')
    expect(screen.queryByText(/Estimated cost/)).not.toBeInTheDocument()
  })
})

describe('refused requests', () => {
  it('lists each refusal by route template and count', async () => {
    page()
    const refused = await screen.findByText('/api/students/{student_id}/report', { exact: false })
    const row = refused.closest('tr')
    expect(within(row).getByText(/Refused access/)).toBeInTheDocument()
    expect(within(row).getByText('2')).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(REFUSALS_PATH)
  })

  it('says an empty day was read', async () => {
    overrideApi(REFUSALS_PATH, () => buildRefusals({ refusals: [], recent: [] }))
    page()
    expect(await screen.findByText(/No refused requests in the last 24 hours/)).toBeInTheDocument()
  })

  it('never shows an unread store as no refusals', async () => {
    overrideApi(REFUSALS_PATH, () => buildRefusals({ retrieved: false, refusals: [], recent: [] }))
    page()
    expect(await screen.findByText(/Stored refusal counts could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No refused requests/)).not.toBeInTheDocument()
  })

  it('shows a failed request as a failed request', async () => {
    overrideApi(REFUSALS_PATH, () => { throw new Error('network down') })
    page()
    const refusals = await screen.findByRole('heading', { name: 'Refused requests' })
    expect(await within(refusals.closest('section')).findByRole('status')).toBeInTheDocument()
    expect(screen.queryByText(/No refused requests/)).not.toBeInTheDocument()
  })
})

describe("the Overview's card", () => {
  it('shows only this hour and the last, not the whole day', async () => {
    render(<MemoryRouter><RefusalSummary /></MemoryRouter>)
    expect(await screen.findByText(/\/api\/generate-question/)).toBeInTheDocument()
    expect(screen.queryByText(/\{student_id\}\/report/)).not.toBeInTheDocument()
  })

  it('names its own counts and the refusal, not the page or an outage', async () => {
    overrideApi(REFUSALS_PATH, () => { throw apiError(403) })
    render(<MemoryRouter><RefusalSummary /></MemoryRouter>)
    expect(await screen.findByText("You don't have access to refusal counts.")).toBeInTheDocument()
    expect(screen.queryByText(/backend is running/)).not.toBeInTheDocument()
  })

  it('keeps the counts it has when a later refresh fails, and says so', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<MemoryRouter><RefusalSummary /></MemoryRouter>)
      expect(await screen.findByText(/\/api\/generate-question/)).toBeInTheDocument()

      overrideApi(REFUSALS_PATH, () => { throw apiError(503) })
      await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })

      expect(screen.getByText(/The last refresh failed \(503\)/)).toBeInTheDocument()
      expect(screen.getByText(/\/api\/generate-question/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('what the counts can and cannot say', () => {
  it('shows no call ceiling where none applies', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({ provider: 'ollama', model: null, daily_ceiling: null }))
    page()
    await screen.findByText('26')
    expect(screen.queryByText(/Daily call ceiling/)).not.toBeInTheDocument()
  })

  it('says a read that stopped at the row cap is short', async () => {
    overrideApi(REFUSALS_PATH, () => buildRefusals({ complete: false }))
    page()
    expect(await screen.findByText(/Only part of this period could be read/)).toBeInTheDocument()
  })

  it('does not call a pushed-out prefetch one whose lesson ended', async () => {
    overrideApi(GENERATION_PATH, () => buildGeneration({
      questions: { 'served:inline': 5, 'served:queue': 15, prefetched: 16, prefetch_discarded: 3 },
    }))
    page()
    expect(await screen.findByText(/3 discarded because their lesson ended or its queue was full/))
      .toBeInTheDocument()
  })
})
