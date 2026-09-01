/**
 * The picture a question carries, drawn from its spec.
 *
 * The backend stores a specification rather than markup (`question_figures.py`,
 * `questions.figure`), and this is the only place that turns one into pixels.
 * Two things follow from that and both are load-bearing:
 *
 *  - **The drawing and the description come from the same object.** A screen
 *    reader is given a sentence built from the same `rows` and `columns` the
 *    squares are drawn from, so the two cannot describe different pictures.
 *    That is the rule `AccessibleChart` exists for -- its chart and its
 *    `sr-only` table were separate literals and drifted twice in one PR -- and
 *    it matters more here, because a figure has no text of its own for any
 *    check to compare.
 *
 *  - **An unknown type renders nothing rather than throwing.** A figure is an
 *    enrichment; the question text is complete without it ("a rectangle split
 *    into 3 rows of 4 same-size squares"). A client that crashed on a spec it
 *    did not recognise would take the whole question down to avoid drawing a
 *    picture, which is the wrong way round -- and it would do so on exactly the
 *    deployment running an older bundle against a newer bank.
 *
 * `role="img"` with a label, not a bare `<svg>`: WAI-ARIA prunes the children
 * of an `img`, which is right here -- a grid of squares has no meaning
 * element-by-element -- and it is why the label has to carry the whole
 * description rather than leaning on anything inside.
 */

const MAX_SIDE = 12          // matches question_figures.MAX_GRID_SIDE
const CELL = 24
const PAD = 2

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function RectGrid({ rows, columns }) {
  const cells = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < columns; c++) {
      cells.push(
        <rect
          key={`${r}-${c}`}
          x={PAD + c * CELL}
          y={PAD + r * CELL}
          width={CELL}
          height={CELL}
          // `currentColor` rather than a fixed grey: the card flips with the
          // theme, and a figure drawn in one mode's ink is invisible in the
          // other's.
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        />
      )
    }
  }
  const width = columns * CELL + PAD * 2
  const height = rows * CELL + PAD * 2
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="max-w-full h-auto text-gray-700 dark:text-gray-300"
      focusable="false"
      aria-hidden="true"
    >
      {cells}
    </svg>
  )
}

/** The sentence, from the same numbers the squares are drawn from. */
function describe(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return `A rectangle split into ${plural(figure.rows, 'row')} of ` +
             `${plural(figure.columns, 'equal square')}.`
    default:
      return null
  }
}

function draw(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return <RectGrid rows={figure.rows} columns={figure.columns} />
    default:
      return null
  }
}

function usable(figure) {
  if (!figure || typeof figure !== 'object') return false
  if (figure.type === 'rect_grid') {
    // The same bound the backend applies. Checked again here because a bank row
    // outlives the code that wrote it, and a 40x40 grid is 1600 rects in a
    // question card.
    return [figure.rows, figure.columns].every(
      n => Number.isInteger(n) && n >= 1 && n <= MAX_SIDE)
  }
  return false
}

export default function QuestionFigure({ figure }) {
  if (!usable(figure)) return null
  const description = describe(figure)
  const picture = draw(figure)
  if (!description || !picture) return null
  return (
    <div className="my-4 flex justify-center">
      <div role="img" aria-label={description}>
        {picture}
      </div>
    </div>
  )
}
