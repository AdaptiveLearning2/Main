import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import { TODAY_PATH, buildToday } from '../../test/fixtures/adminLifecycle'
import Today from './Today'

beforeEach(() => {
  resetApi()
  mockApi({ [TODAY_PATH]: buildToday() })
})

describe("today's counts", () => {
  it('shows what was asked of the backend and its counts', async () => {
    render(<Today />)
    expect(await screen.findByText('250')).toBeInTheDocument()   // 214 adaptive + 36 practice
    expect(screen.getByText('214 adaptive, 36 practice')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText(/America\/Chicago/)).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith(TODAY_PATH)
  })

  it('says the answer count is adaptive only when practice answers could not be read', async () => {
    overrideApi(TODAY_PATH, () => buildToday({ practice_answers: null }))
    render(<Today />)
    expect(await screen.findByText('214')).toBeInTheDocument()
    expect(screen.getByText('adaptive only: practice could not be read')).toBeInTheDocument()
  })

  it('says teacher and parent activity is not recorded rather than showing a zero', async () => {
    render(<Today />)
    expect(await screen.findByText(/Teacher and parent activity is not recorded/)).toBeInTheDocument()
  })

  it('never shows an unread count as a quiet day', async () => {
    overrideApi(TODAY_PATH, () => buildToday({ retrieved: false, counts: null }))
    render(<Today />)
    expect(await screen.findByText(/Today's session counts could not be read/)).toBeInTheDocument()
    expect(screen.getByText(/How today.s lessons ended could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No lesson has ended today/)).not.toBeInTheDocument()
  })

  it('names a refusal as one, not as the backend being down', async () => {
    overrideApi(TODAY_PATH, () => { throw apiError(403) })
    render(<Today />)
    expect(await screen.findByText("You don't have access to today's counts.")).toBeInTheDocument()
  })
})

describe('how lessons ended', () => {
  it('names each reason in words, most frequent first', async () => {
    render(<Today />)
    const items = await screen.findAllByRole('listitem')
    expect(items[0]).toHaveTextContent('5 × Finished by the student')
    expect(screen.getByText(/Closed by the sweep as abandoned/)).toBeInTheDocument()
    expect(screen.getByText(/Ended before reasons were recorded/)).toBeInTheDocument()
    expect(screen.queryByText(/page_closed/)).not.toBeInTheDocument()
  })

  it('says a day with no ended lesson was read', async () => {
    overrideApi(TODAY_PATH, () => buildToday({
      counts: { ...buildToday().counts, ended_by_reason: {} }, discarded: {},
    }))
    render(<Today />)
    expect(await screen.findByText('No lesson has ended today.')).toBeInTheDocument()
  })

  it('counts discarded empty lessons, which leave no row, apart from the ended ones', async () => {
    render(<Today />)
    expect(await screen.findByText(/Also discarded, having recorded nothing: 2 lesson page closed or left/))
      .toBeInTheDocument()
  })

  it('says the discards could not be counted when the counters were unreadable', async () => {
    overrideApi(TODAY_PATH, () => buildToday({ discarded: {}, discarded_retrieved: false }))
    render(<Today />)
    expect(await screen.findByText(/Discarded empty lessons could not be counted/)).toBeInTheDocument()
  })
})
