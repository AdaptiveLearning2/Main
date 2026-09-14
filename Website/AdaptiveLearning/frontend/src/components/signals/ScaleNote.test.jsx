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
    expect(isMixedScale(combineScales([{ score_scale: { min: 2, max: 2 } }]))).toBe(false)
  })
})
