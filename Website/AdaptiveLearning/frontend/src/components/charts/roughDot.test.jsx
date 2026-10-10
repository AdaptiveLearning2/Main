import { render } from '@testing-library/react'
import { roughDot, seriesDot } from './roughDot'

const series = { colour: '#ea580c', rough: r => r.few === true }

function dot(props) {
  const Dot = roughDot(series)
  const { container } = render(<svg><Dot cx={10} cy={20} index={0} {...props} /></svg>)
  return container.querySelector('circle')
}

describe('roughDot', () => {
  it('draws a rough point hollow, ringed in the series colour', () => {
    const c = dot({ payload: { few: true } })
    expect(c).not.toHaveAttribute('fill')
    expect(c).toHaveClass('fill-slate-50')
    expect(c).toHaveAttribute('stroke', '#ea580c')
    expect(c).toHaveAttribute('stroke-width', '1.5')
  })

  it('draws an ordinary point filled', () => {
    const c = dot({ payload: { few: false } })
    expect(c).toHaveAttribute('fill', '#ea580c')
    expect(c).not.toHaveClass('fill-slate-50')
  })

  it('draws nothing for a gap', () => {
    expect(dot({ cy: null, payload: { few: true } })).toBeNull()
  })
})

describe('seriesDot', () => {
  it("fills a series without a rough test, overriding Recharts' white centre", () => {
    expect(seriesDot({ colour: '#6366f1' })).toEqual({ r: 3, fill: '#6366f1', strokeWidth: 0 })
  })

  it('hands a series with a rough test the per-point renderer', () => {
    expect(seriesDot(series)).toBeTypeOf('function')
  })
})
