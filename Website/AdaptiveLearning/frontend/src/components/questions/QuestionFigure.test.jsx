import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import QuestionFigure from './QuestionFigure'

describe('QuestionFigure', () => {
  it('describes the picture with the numbers it draws', () => {
    // Sentence and drawing come from one object, so they cannot disagree.
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
    // An older bundle against a newer bank must not lose the whole question.
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
    const { container } = render(
      <QuestionFigure figure={{ type: 'rect_grid', rows: 2, columns: 2 }} />)
    const strokes = [...container.querySelectorAll('rect')]
      .map(r => r.getAttribute('stroke'))
    expect(new Set(strokes)).toEqual(new Set(['currentColor']))
    expect(container.querySelector('svg').getAttribute('class'))
      .toMatch(/dark:/)
  })

  it('hides the svg from the reader that already has the label', () => {
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
    // A summary would give a screen-reader user a different question.
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
    // 1.MD.4 asks for counts, not comparisons.
    const { container } = render(<QuestionFigure figure={PETS} />)
    expect(container.querySelectorAll('line')).toHaveLength(7)  // 0..6
  })

  it('spaces the columns so long labels cannot collide', () => {
    // A column is at least as wide as its widest label needs.
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
    // Naming the fraction would answer the question for a screen-reader user.
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
    // Filled versus outlined, not two colours: survives greyscale print.
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

// The backend's kindergarten tables, read from source: a bundle cannot import Python.
const FIGURES_PY = readFileSync(resolve(fileURLToPath(import.meta.url),
  '..', '..', '..', '..', '..', 'backend', 'question_figures.py'), 'utf8')
const BACKEND_ITEMS = [...(FIGURES_PY.match(/^FIGURE_ITEMS = \(([\s\S]*?)\)/m)?.[1] ?? '')
  .matchAll(/"([a-z]+)"/g)].map(m => m[1])
const BACKEND_SIDES = Object.fromEntries([...(FIGURES_PY.match(/^SHAPE_SIDES = \{(.*)\}/m)?.[1] ?? '')
  .matchAll(/"([a-z]+)": (\d+)/g)].map(m => [m[1], Number(m[2])]))

describe('a kindergarten picture to count', () => {
  it('names each picture once, so a listener counts them as a looker does', () => {
    // A number would answer the question for a screen-reader user.
    const { container } = render(<QuestionFigure figure={{ type: 'objects',
      groups: [{ item: 'apple', count: 3 }] }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('A picture of apples: apple, apple, apple.')
    expect(container.querySelectorAll('span')).toHaveLength(3)
  })

  it('draws two groups for a comparison, each counted separately', () => {
    const { container } = render(<QuestionFigure figure={{ type: 'objects',
      groups: [{ item: 'fish', count: 2 }, { item: 'star', count: 1 }] }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'A picture of fish: fish, fish. stars: star.')
    expect(container.querySelectorAll('span')).toHaveLength(3)
  })

  it('has a picture for every item the backend can ask about', () => {
    expect(BACKEND_ITEMS.length, 'FIGURE_ITEMS not found -- this check is inert').toBeGreaterThan(0)
    for (const item of BACKEND_ITEMS) {
      const { unmount } = render(<QuestionFigure figure={{ type: 'objects', groups: [{ item, count: 1 }] }} />)
      expect(screen.getByRole('img'), item).toBeInTheDocument()
      unmount()
    }
  })

  it.each([
    ['an item it has no picture for', { type: 'objects', groups: [{ item: 'dragon', count: 2 }] }],
    ['more than it will draw', { type: 'objects', groups: [{ item: 'apple', count: 21 }] }],
    ['a group of nothing', { type: 'objects', groups: [{ item: 'apple', count: 0 }] }],
    ['two groups of one item', { type: 'objects', groups: [{ item: 'apple', count: 2 }, { item: 'apple', count: 3 }] }],
    ['three groups', { type: 'objects', groups: [{ item: 'apple', count: 1 }, { item: 'star', count: 1 }, { item: 'car', count: 1 }] }],
  ])('renders nothing for %s', (_label, figure) => {
    const { container } = render(<QuestionFigure figure={figure} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('ten frames', () => {
  it('fills a whole frame before the next and says so', () => {
    const { container } = render(<QuestionFigure figure={{ type: 'ten_frames', count: 14 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Two ten frames: the first full with 10 dots, the second with 4 dots.')
    expect(container.querySelectorAll('rect')).toHaveLength(20)
    expect(container.querySelectorAll('circle')).toHaveLength(14)
  })

  it('draws one frame for ten or fewer', () => {
    const { container } = render(<QuestionFigure figure={{ type: 'ten_frames', count: 1 }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('A ten frame with 1 dot.')
    expect(container.querySelectorAll('rect')).toHaveLength(10)
  })

  it.each([0, 21, 2.5])('renders nothing for a count of %s', count => {
    const { container } = render(<QuestionFigure figure={{ type: 'ten_frames', count }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('a flat shape', () => {
  it('draws every shape the backend can ask about, with its own number of sides', () => {
    expect(Object.keys(BACKEND_SIDES).length, 'SHAPE_SIDES not found').toBeGreaterThan(0)
    for (const [shape, sides] of Object.entries(BACKEND_SIDES)) {
      const { container, unmount } = render(
        <QuestionFigure figure={{ type: 'shape', shape, named: false }} />)
      const polygon = container.querySelector('polygon')
      expect(polygon ? polygon.getAttribute('points').split(' ').length : 0, shape).toBe(sides)
      unmount()
    }
  })

  it('keeps the name out of the description when the question asks for it', () => {
    render(<QuestionFigure figure={{ type: 'shape', shape: 'hexagon', named: false }} />)
    const img = screen.getByRole('img')
    expect(img).toHaveAccessibleName('A picture of a flat shape with 6 straight sides.')
    expect(img.getAttribute('aria-label')).not.toMatch(/hexagon/)
  })

  it('names the shape when the question asks about its sides', () => {
    render(<QuestionFigure figure={{ type: 'shape', shape: 'triangle', named: true }} />)
    expect(screen.getByRole('img')).toHaveAccessibleName('A picture of a triangle.')
  })

  it.each([
    ['a shape it does not draw', { type: 'shape', shape: 'star', named: true }],
    ['no word on naming it', { type: 'shape', shape: 'square' }],
  ])('renders nothing for %s', (_label, figure) => {
    const { container } = render(<QuestionFigure figure={figure} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('every surface that presents a question', () => {
  // Exhaustive: a question without its figure is a different question.
  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')

  // A clamped reference to a question, not the question itself.
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
    // `<QuestionFigure`, not the bare name, which a dangling import satisfies.
    const missing = presenting.filter(
      f => !readFileSync(f, 'utf8').includes('<QuestionFigure'))
    expect(missing).toEqual([])
  })
})
