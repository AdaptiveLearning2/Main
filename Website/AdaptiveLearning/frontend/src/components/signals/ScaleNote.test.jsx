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
      .toEqual({ min: 1, max: 2 })
    expect(combineScales([{}, { score_scale: null }])).toBeNull()
    // A half-populated row is unknown, not a change.
    expect(combineScales([{ score_scale: { min: 1 } }])).toBeNull()
    expect(isMixedScale(combineScales([{ score_scale: { min: 1 } }, { score_scale: { min: 1, max: 1 } }]))).toBe(false)
    expect(isMixedScale(combineScales([{ score_scale: { min: 2, max: 2 } }]))).toBe(false)
  })

  it('names a version step for 1..2 and does not disclaim focus for 2..3', () => {
    const { rerender } = render(<ScaleNote scale={{ min: 1, max: 2 }} what="These" />)
    expect(screen.getByRole('note')).toHaveTextContent(/focus and stress scores/)
    expect(screen.getByRole('note')).toHaveTextContent(/before and after/)
    // Scale 3 (local calm) moves stress, not focus, and runs beside scale 2.
    rerender(<ScaleNote scale={{ min: 2, max: 3 }} what="These" />)
    expect(screen.getByRole('note')).not.toHaveTextContent(/focus/)
    expect(screen.getByRole('note')).not.toHaveTextContent(/before and after/)
    expect(screen.getByRole('note')).toHaveTextContent(/two different ways/)
    expect(screen.getByRole('note')).toHaveTextContent(/not comparable/)
    rerender(<ScaleNote scale={{ min: 1, max: 3 }} what="These" />)
    expect(screen.getByRole('note')).toHaveTextContent(/focus and stress scores/)
    expect(screen.getByRole('note')).toHaveTextContent(/two different ways/)
    rerender(<ScaleNote scale={{ min: 3, max: 3 }} what="These" />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
