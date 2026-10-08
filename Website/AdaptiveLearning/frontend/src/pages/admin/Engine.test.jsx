import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import {
  GENERATION_PATH, REFUSALS_PATH, buildGeneration, buildRefusals,
} from '../../test/fixtures/opsCounters'
import AdminEngine, { RefusalSummary } from './Engine'

beforeEach(() => {
  resetApi()
  mockApi({ [GENERATION_PATH]: buildGeneration(), [REFUSALS_PATH]: buildRefusals() })
})

const page = () => render(<MemoryRouter><AdminEngine /></MemoryRouter>)
const section = name => screen.getByRole('heading', { name }).closest('section')

describe('question generation', () => {
  it('asks for the last 24 hours and shows the totals and the ceiling it was given', async () => {
    page()
    expect(await screen.findByText('20')).toBeInTheDocument()   // 5 inline + 15 queued

    const gen = section('Question generation')
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
    await screen.findByText('20')
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
})
