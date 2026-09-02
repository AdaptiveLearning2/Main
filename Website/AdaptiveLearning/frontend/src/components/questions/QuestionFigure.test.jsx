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

describe('a bar chart', () => {
  const PETS = { type: 'bar_chart', bars: [
    { label: 'cats', value: 6 }, { label: 'dogs', value: 4 },
  ] }

  it('names every bar and its height, not a summary', () => {
    // The question asks the reader to compare bars, so "a bar graph of two
    // categories" is not the same information -- a screen-reader user would
    // have a different question from a sighted one.
    render(<QuestionFigure figure={PETS} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A bar graph showing cats: 6, dogs: 4.')
  })

  it('draws a bar per category, in proportion', () => {
    const { container } = render(<QuestionFigure figure={PETS} />)
    const heights = [...container.querySelectorAll('rect')]
      .map(r => Number(r.getAttribute('height')))
    expect(heights).toHaveLength(2)
    expect(heights[0] / heights[1]).toBe(6 / 4)
  })

  it('rules every unit so the bars can be counted rather than estimated', () => {
    // Which is the reading 1.MD.4 asks for. Without gridlines a student can
    // compare two bars but cannot say how many.
    const { container } = render(<QuestionFigure figure={PETS} />)
    expect(container.querySelectorAll('line')).toHaveLength(7)  // 0..6
  })

  it('spaces the columns so long labels cannot collide', () => {
    // Found by rendering it and looking, not by a test: at a fixed column
    // width "storybooks" and "picture books" printed on top of each other.
    // Both labels were present and correct in the DOM, so nothing here could
    // see it -- on a figure whose entire job is to be read.
    //
    // The invariant the fix establishes: a column is at least as wide as its
    // widest label needs.
    const bars = [{ label: 'storybooks', value: 5 },
                  { label: 'picture books', value: 7 }]
    const { container } = render(<QuestionFigure figure={{ type: 'bar_chart', bars }} />)
    const xs = [...container.querySelectorAll('text')]
      .map(t => Number(t.getAttribute('x')))
    const widest = Math.max(...bars.map(b => b.label.length))
    const needed = widest * 11 * 0.58        // LABEL_PX * CHAR_W
    expect(xs[1] - xs[0]).toBeGreaterThanOrEqual(needed)
  })

  it.each([
    ['one bar, which is not a comparison', { type: 'bar_chart', bars: [{ label: 'cats', value: 3 }] }],
    ['more bars than the backend will build', { type: 'bar_chart', bars: Array.from({ length: 6 }, (_, i) => ({ label: `c${i}`, value: 2 })) }],
    ['a bar taller than the backend allows', { type: 'bar_chart', bars: [{ label: 'a', value: 99 }, { label: 'b', value: 2 }] }],
    ['a fractional height', { type: 'bar_chart', bars: [{ label: 'a', value: 2.5 }, { label: 'b', value: 2 }] }],
    ['a nameless bar', { type: 'bar_chart', bars: [{ label: '', value: 2 }, { label: 'b', value: 2 }] }],
    ['bars that are not a list', { type: 'bar_chart', bars: 'cats: 6' }],
  ])('renders nothing for %s', (_label, figure) => {
    // Re-checked client-side because a bank row outlives the code that wrote it.
    const { container } = render(<QuestionFigure figure={figure} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('a partitioned shape', () => {
  it('says how many parts and how many are shaded, not the fraction', () => {
    // Naming "three quarters" would answer the question for a screen-reader
    // user that a sighted one has to read off the picture -- two different
    // questions from the same page.
    render(<QuestionFigure figure={{ type: 'part_whole', parts: 4, shaded: 3 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A shape split into 4 equal parts, 3 of them shaded.')
  })

  it('says "1 equal part" rather than "1 equal parts"', () => {
    render(<QuestionFigure figure={{ type: 'part_whole', parts: 2, shaded: 1 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A shape split into 2 equal parts, 1 of them shaded.')
  })

  it('fills the shaded parts and outlines the rest', () => {
    // Filled versus outlined, not two colours: it survives being printed in
    // black and white and does not depend on telling two hues apart.
    const { container } = render(
      <QuestionFigure figure={{ type: 'part_whole', parts: 4, shaded: 3 }} />)
    const fills = [...container.querySelectorAll('rect')]
      .map(r => r.getAttribute('fill'))
    expect(fills).toEqual(['currentColor', 'currentColor', 'currentColor', 'none'])
  })

  it.each([
    ['every part shaded, which is the whole shape', { type: 'part_whole', parts: 4, shaded: 4 }],
    ['nothing shaded', { type: 'part_whole', parts: 4, shaded: 0 }],
    ['one part, which is not a partition', { type: 'part_whole', parts: 1, shaded: 1 }],
    ['more parts than the backend will build', { type: 'part_whole', parts: 12, shaded: 5 }],
    ['a fractional count', { type: 'part_whole', parts: 4.5, shaded: 2 }],
  ])('renders nothing for %s', (_label, figure) => {
    const { container } = render(<QuestionFigure figure={figure} />)
    expect(container).toBeEmptyDOMElement()
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
    // `<QuestionFigure`, not `includes('QuestionFigure')`: the name alone is
    // satisfied by a dangling import, which is precisely what removing the
    // element leaves behind. The first version of this check passed against a
    // build with the render deleted and the import kept. `no-unused-vars`
    // would catch that, but lint is non-blocking in CI here, so it would land.
    const missing = presenting.filter(
      f => !readFileSync(f, 'utf8').includes('<QuestionFigure'))
    expect(missing).toEqual([])
  })
})
