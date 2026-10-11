import { render } from '@testing-library/react'
import { LINE_LEGEND, legendText, lineDash } from './legendProps'

describe('chart legends', () => {
  it('draw a line chart\'s icon as the line itself, so a dashed series shows dashed', () => {
    expect(LINE_LEGEND.iconType).toBe('plainline')
    expect(LINE_LEGEND.formatter).toBe(legendText)
  })

  it('pass a dash only when a series has one, never the key with undefined', () => {
    expect(lineDash('6 3')).toEqual({ strokeDasharray: '6 3' })
    expect(lineDash(undefined)).toEqual({})
    expect('strokeDasharray' in lineDash(undefined)).toBe(false)
  })

  it('keep legend text in a text colour, not the series colour', () => {
    const { container } = render(legendText('Calm'))
    const span = container.querySelector('span')
    expect(span).toHaveTextContent('Calm')
    expect(span).toHaveClass('text-gray-700', 'dark:text-gray-300')
  })
})
