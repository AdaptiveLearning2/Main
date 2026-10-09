import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import { ADAPTIVE_PATH, buildAdaptive } from '../../test/fixtures/adminAdaptive'
import AdaptiveDecisions from './Adaptive'

beforeEach(() => {
  resetApi()
  mockApi({ [ADAPTIVE_PATH]: buildAdaptive() })
})

describe('adaptive decisions', () => {
  it('shows each day by direction and names every reason in words, most frequent first', async () => {
    render(<AdaptiveDecisions />)
    const row = (await screen.findByText('2026-10-08')).closest('tr')
    expect(row).toHaveTextContent('6 eased, 14 raised, 40 held')
    const reasons = within(row).getAllByRole('listitem').map(li => li.textContent)
    expect(reasons[0]).toBe('30 × Held: nothing to act on')
    expect(reasons).toContain('14 × Raised: a run of correct answers')
    expect(row).not.toHaveTextContent(/nothing_to_act_on|correct_run/)
    expect(apiFetch).toHaveBeenCalledWith(ADAPTIVE_PATH)
  })

  it('shows a day under the floor as withheld, with no figure', async () => {
    render(<AdaptiveDecisions />)
    const row = (await screen.findByText('2026-10-07')).closest('tr')
    expect(row).toHaveTextContent('Withheld: fewer than 5 students')
    expect(row).not.toHaveTextContent(/eased|×/)
  })

  it('says a fortnight with no adaptive question was read', async () => {
    overrideApi(ADAPTIVE_PATH, () => buildAdaptive({ decisions: [] }))
    render(<AdaptiveDecisions />)
    expect(await screen.findByText('No adaptive decision recorded in the last 14 days.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('never shows an unread answer as a quiet fortnight', async () => {
    overrideApi(ADAPTIVE_PATH, () => buildAdaptive({ retrieved: false, decisions: [] }))
    render(<AdaptiveDecisions />)
    expect(await screen.findByText(/Adaptive decisions could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No adaptive decision/)).not.toBeInTheDocument()
  })

  it('says how many decisions failed to store, even when none were', async () => {
    overrideApi(ADAPTIVE_PATH, () => buildAdaptive({ decisions: [], write_failures: 12 }))
    render(<AdaptiveDecisions />)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '12 decisions in the last 14 days could not be stored, so the figures here are short by that many.')
  })

  it('says an unread failure count is unread, not zero', async () => {
    overrideApi(ADAPTIVE_PATH, () => buildAdaptive({ write_failures: null }))
    render(<AdaptiveDecisions />)
    expect(await screen.findByText('Failed decision writes could not be read.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('names a refusal as one', async () => {
    overrideApi(ADAPTIVE_PATH, () => { throw apiError(403) })
    render(<AdaptiveDecisions />)
    expect(await screen.findByText("You don't have access to adaptive decisions.")).toBeInTheDocument()
  })
})
