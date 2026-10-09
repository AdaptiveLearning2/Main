import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiError, apiFetch, mockApi, overrideApi, resetApi } from '../../test/mocks/apiFetch'
import {
  CONSENT_OPS_PATH, FUNNEL_PATH, QUALITY_PATH, buildConsentOps, buildFunnel, buildSignalQuality, heartDay,
} from '../../test/fixtures/adminTotals'
import { ConsentChanges, Funnel, SignalQuality } from './Totals'

beforeEach(() => {
  resetApi()
  mockApi({
    [FUNNEL_PATH]: buildFunnel(), [CONSENT_OPS_PATH]: buildConsentOps(), [QUALITY_PATH]: buildSignalQuality(),
  })
})

describe('the adoption funnel', () => {
  it('shows each role with each step as a count and a share of those who signed up', async () => {
    render(<Funnel />)
    const step = (await screen.findByText('Had a headband lesson')).closest('li')
    expect(step).toHaveTextContent('12')
    expect(step).toHaveTextContent('(40%)')
    expect(screen.getByText('Has a student in a class').closest('li')).toHaveTextContent('1(50%)')
    expect(apiFetch).toHaveBeenCalledWith(FUNNEL_PATH)
  })

  it('never shows an unread funnel as a school with no one in it', async () => {
    overrideApi(FUNNEL_PATH, () => buildFunnel({ retrieved: false, funnel: null }))
    render(<Funnel />)
    expect(await screen.findByText(/The adoption funnel could not be read/)).toBeInTheDocument()
  })

  it('names a refusal as one', async () => {
    overrideApi(FUNNEL_PATH, () => { throw apiError(403) })
    render(<Funnel />)
    expect(await screen.findByText("You don't have access to the adoption funnel.")).toBeInTheDocument()
  })
})

describe('consent changes', () => {
  it('lists the latest week in words and totals every week by kind', async () => {
    render(<ConsentChanges />)
    expect(await screen.findByText(/× EEG withdrawn/)).toBeInTheDocument()
    expect(screen.getByText(/× Headband heart erased/)).toBeInTheDocument()
    // The earlier week's turn-ons are in the table, not the latest week's list.
    expect(screen.queryByText(/× Camera turned on by a parent/)).not.toBeInTheDocument()
    const row = screen.getByText('2026-09-28').closest('tr')
    expect(within(row).getAllByRole('cell').map(c => c.textContent)).toEqual(['2026-09-28', '0', '3', '0'])
  })

  it('says how far back turn-ons go and that erasures are a lower bound', async () => {
    render(<ConsentChanges />)
    expect(await screen.findByText(/recorded from 2026-09-28/)).toBeInTheDocument()
    expect(screen.getByText(/lower bound/)).toBeInTheDocument()
  })

  it('says a quiet stretch was read', async () => {
    overrideApi(CONSENT_OPS_PATH, () => buildConsentOps({ changes: [] }))
    render(<ConsentChanges />)
    expect(await screen.findByText('No consent change in the last 8 weeks.')).toBeInTheDocument()
  })

  it('never shows unread consent history as a quiet stretch', async () => {
    overrideApi(CONSENT_OPS_PATH, () => buildConsentOps({ retrieved: false, changes: [] }))
    render(<ConsentChanges />)
    expect(await screen.findByText(/Consent changes could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No consent change/)).not.toBeInTheDocument()
  })
})

describe('signal quality', () => {
  it('shows a day under the floor as withheld, with no figure', async () => {
    render(<SignalQuality />)
    const row = (await screen.findByText('2026-10-07')).closest('tr')
    expect(row).toHaveTextContent('Withheld: fewer than 5 students')
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument()
  })

  it('shows the share of usable EEG readings', async () => {
    render(<SignalQuality />)
    const eeg = (await screen.findByText('EEG')).closest('table')
    expect(within(eeg).getAllByText('2026-10-08')[0].closest('tr')).toHaveTextContent('85% of 5400')
  })

  it('shows heart quality, calibration time and the simulated share per sensor', async () => {
    render(<SignalQuality />)
    const heart = (await screen.findByText('Heart')).closest('table')
    const row = within(heart).getByText('Headband').closest('tr')
    // Deciles 9 and 10 are 0.8 and up: 700 of 800.
    expect(row).toHaveTextContent('88%')
    expect(row).toHaveTextContent('95 s / 141 s')
    expect(row).toHaveTextContent('1 of 8')
    expect(row).toHaveTextContent('50%')
    expect(within(heart).getByText('Camera').closest('tr')).toHaveTextContent('Withheld')
  })

  it('says a fortnight with no signal was read', async () => {
    overrideApi(QUALITY_PATH, () => buildSignalQuality({ eeg: [], heart: [] }))
    render(<SignalQuality />)
    expect(await screen.findByText('No signal recorded in the last 14 days.')).toBeInTheDocument()
  })

  it('never shows unread quality as no signal', async () => {
    overrideApi(QUALITY_PATH, () => buildSignalQuality({ retrieved: false, eeg: [], heart: [] }))
    render(<SignalQuality />)
    expect(await screen.findByText(/Signal quality could not be read/)).toBeInTheDocument()
    expect(screen.queryByText(/No signal recorded/)).not.toBeInTheDocument()
  })

  it('shows a dash rather than a share of nothing', async () => {
    overrideApi(QUALITY_PATH, () => buildSignalQuality({ heart: [heartDay({ sqi_deciles: {} })] }))
    render(<SignalQuality />)
    const row = (await screen.findByText('Headband')).closest('tr')
    expect(within(row).getAllByRole('cell')[3]).toHaveTextContent('—')
  })
})
