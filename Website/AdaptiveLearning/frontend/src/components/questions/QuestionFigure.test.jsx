import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import QuestionFigure from './QuestionFigure'

describe('QuestionFigure', () => {
  it('describes the picture with the numbers it draws', () => {
    // The rule AccessibleChart exists for: the sentence and the drawing come
    // from one object, so they cannot describe different pictures. A figure
    // has no text of its own for any other check to compare.
    const { container } = render(
      <QuestionFigure figure={{ type: 'rect_grid', rows: 3, columns: 4 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A rectangle split into 3 rows of 4 equal squares.')
    expect(container.querySelectorAll('rect')).toHaveLength(12)
  })

  it('says "1 row" and "1 square", not "1 rows"', () => {
    render(<QuestionFigure figure={{ type: 'rect_grid', rows: 1, columns: 1 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A rectangle split into 1 row of 1 equal square.')
  })

  it('renders nothing for a type it does not know', () => {
    // A figure is an enrichment and the question text is complete without it.
    // Throwing here would take the whole question down to avoid drawing a
    // picture -- on exactly the deployment running an older bundle against a
    // newer bank.
    const { container } = render(
      <QuestionFigure figure={{ type: 'number_line', from: 0, to: 10 }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it.each([
    ['no figure at all', undefined],
    ['an explicit null', null],
    ['a spec that is not an object', 'rect_grid'],
    ['a side of zero', { type: 'rect_grid', rows: 0, columns: 4 }],
    ['a negative side', { type: 'rect_grid', rows: -1, columns: 4 }],
    ['a fractional side', { type: 'rect_grid', rows: 2.5, columns: 4 }],
    ['a grid too large to read', { type: 'rect_grid', rows: 40, columns: 40 }],
    ['a missing dimension', { type: 'rect_grid', rows: 3 }],
  ])('renders nothing for %s', (_label, figure) => {
    const { container } = render(<QuestionFigure figure={figure} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('draws in the current text colour so it survives both themes', () => {
    // A figure drawn in one mode's ink is invisible in the other's; the card
    // this sits in flips.
    const { container } = render(
      <QuestionFigure figure={{ type: 'rect_grid', rows: 2, columns: 2 }} />)
    const strokes = [...container.querySelectorAll('rect')]
      .map(r => r.getAttribute('stroke'))
    expect(new Set(strokes)).toEqual(new Set(['currentColor']))
    expect(container.querySelector('svg').getAttribute('class'))
      .toMatch(/dark:/)
  })

  it('hides the svg from the reader that already has the label', () => {
    // role="img" prunes its children, so an svg left exposed would be a second
    // announcement of nothing.
    const { container } = render(
      <QuestionFigure figure={{ type: 'rect_grid', rows: 2, columns: 2 }} />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('every surface that presents a question', () => {
  // Exhaustiveness, like the backend's close-site and _MODE_AWARE tests, and
  // for the same reason: this shipped wired into two of five surfaces. A
  // question rendered without its figure is not a smaller version of the
  // question -- "3 rows of 4 same-size squares" with nothing to count is a
  // different question, and the student answering it in one place and the
  // teacher reviewing it in another see different things.
  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')

  // Presents a *reference* to a question rather than the question: a
  // `line-clamp-2` row in a "Recent Questions" list, with no options and no
  // way to answer. A figure there is noise, not completeness.
  const REFERENCES_ONLY = [resolve(root, 'pages', 'teacher', 'Dashboard.jsx')]

  const walk = (dir) => readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full)
      : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
  })

  it('renders its figure too', () => {
    const presenting = walk(root)
      .filter(f => !REFERENCES_ONLY.includes(f))
      .filter(f => /\{(q|data|question)\.(question_text|text)\}/.test(
        readFileSync(f, 'utf8')))
    expect(presenting.length).toBeGreaterThan(0)
    const missing = presenting.filter(
      f => !readFileSync(f, 'utf8').includes('QuestionFigure'))
    expect(missing).toEqual([])
  })
})
