import { render, screen } from '@testing-library/react'
import ScaleNote from './ScaleNote'
import { combineScales, isMixedScale } from '../../lib/scoreScale'

describe('ScaleNote', () => {
  it('says so only when a payload straddles the score-scale change', () => {
    const { rerender } = render(<ScaleNote scale={{ min: 1, max: 2 }} />)
    expect(screen.getByRole('note')).toHaveTextContent(/not comparable/)
    rerender(<ScaleNote scale={{ min: 2, max: 2 }} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    rerender(<ScaleNote scale={null} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('combines per-row ranges into the widest and treats unlabelled rows as unknown', () => {
    expect(combineScales([{ score_scale: { min: 1, max: 1 } }, { score_scale: { min: 2, max: 2 } }]))
      .toEqual({ min: 1, max: 2, calm_sources: [] })
    // Calm sources union; one version on two sources is still mixed (stress only).
    const split = combineScales([{ score_scale: { min: 2, max: 2, calm_sources: ['sdk'] } },
                                 { score_scale: { min: 2, max: 2, calm_sources: ['local'] } }])
    expect(split).toEqual({ min: 2, max: 2, calm_sources: ['local', 'sdk'] })
    expect(isMixedScale(split)).toBe(true)
    expect(combineScales([{}, { score_scale: null }])).toBeNull()
    // A half-populated row is unknown, not a change.
    expect(combineScales([{ score_scale: { min: 1 } }])).toBeNull()
    expect(isMixedScale(combineScales([{ score_scale: { min: 1 } }, { score_scale: { min: 1, max: 1 } }]))).toBe(false)
    expect(isMixedScale(combineScales([{ score_scale: { min: 2, max: 2 } }]))).toBe(false)
  })

  it('names a version step for differing ends and a calm-only split for two calm sources', () => {
    const { rerender } = render(<ScaleNote scale={{ min: 1, max: 2 }} what="These" />)
    expect(screen.getByRole('note')).toHaveTextContent(/focus and calm scores/)
    // Named as the page shows it: the stored stress is drawn as calm.
    expect(screen.getByRole('note')).not.toHaveTextContent(/stress/)
    expect(screen.getByRole('note')).toHaveTextContent(/before and after/)
    // The local calm moves stress, not focus, and runs beside the sdk calm on one version.
    const both = ['local', 'sdk']
    rerender(<ScaleNote scale={{ min: 2, max: 2, calm_sources: both }} what="These" />)
    expect(screen.getByRole('note')).not.toHaveTextContent(/focus/)
    expect(screen.getByRole('note')).not.toHaveTextContent(/before and after/)
    expect(screen.getByRole('note')).toHaveTextContent(/two different ways/)
    expect(screen.getByRole('note')).toHaveTextContent(/not comparable/)
    expect(screen.getByRole('note')).not.toHaveTextContent(/stress/)
    rerender(<ScaleNote scale={{ min: 1, max: 2, calm_sources: both }} what="These" />)
    expect(screen.getByRole('note')).toHaveTextContent(/focus and calm scores/)
    expect(screen.getByRole('note')).toHaveTextContent(/two different ways/)
    rerender(<ScaleNote scale={{ min: 2, max: 2, calm_sources: ['local'] }} what="These" />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    // A version number is only a version: a later bump is a step, not a source split.
    rerender(<ScaleNote scale={{ min: 2, max: 3, calm_sources: ['sdk'] }} what="These" />)
    expect(screen.getByRole('note')).toHaveTextContent(/before and after/)
    expect(screen.getByRole('note')).not.toHaveTextContent(/two different ways/)
  })
})
