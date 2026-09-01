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
const MAX_BAR = 20           // matches question_figures.MAX_BAR
const MAX_BARS = 5           // matches question_figures.MAX_CATEGORIES
const CELL = 24
const PAD = 2

const BAR_W = 34
const BAR_GAP = 18
const UNIT = 12              // pixels per unit, so a bar is countable
const AXIS = 22              // room for the label under each bar
const LABEL_PX = 11          // font-size of the category labels
const CHAR_W = 0.58          // ems per character, near enough for sans-serif

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

// `BarGraph`, deliberately: the obvious name is one of the Recharts component
// names `AccessibleChart.test.jsx` matches as a bare word to find charts
// rendered outside it. This is a hand-written `<svg>` -- the case that guard
// states it cannot see -- so the match is a false accusation, and a guard
// that produces those gets switched off. Renaming costs a word; exempting a
// file costs the rule.
//
// The word cannot appear in this comment either, for the same reason: the
// guard reads the file, not the syntax tree.
function BarGraph({ bars }) {
  const tallest = Math.max(...bars.map(b => b.value))
  const plotH = tallest * UNIT
  // The column is as wide as the widest label, not a fixed size. Measured
  // rather than assumed: at a fixed 34px bar and 18px gap, "storybooks" and
  // "picture books" printed on top of each other -- legible in neither, on a
  // figure whose entire job is to be read. Nothing in the DOM tests could see
  // it, since both labels were present and correct.
  const widest = Math.max(...bars.map(b => b.label.length))
  const slot = Math.max(BAR_W + BAR_GAP, widest * LABEL_PX * CHAR_W + 8)
  const width = bars.length * slot + BAR_GAP
  const height = plotH + AXIS + PAD * 2
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="max-w-full h-auto text-gray-700 dark:text-gray-300"
      focusable="false"
      aria-hidden="true"
    >
      {/* Gridlines at every unit, so the bars can be counted rather than
          estimated -- which is the reading 1.MD.4 asks for. */}
      {Array.from({ length: tallest + 1 }, (_, i) => (
        <line
          key={`g${i}`}
          x1={0} x2={width}
          y1={PAD + plotH - i * UNIT} y2={PAD + plotH - i * UNIT}
          stroke="currentColor" strokeWidth="0.5" opacity="0.25"
        />
      ))}
      {bars.map((bar, i) => (
        <g key={bar.label}>
          <rect
            x={BAR_GAP / 2 + i * slot + (slot - BAR_W) / 2}
            y={PAD + plotH - bar.value * UNIT}
            width={BAR_W}
            height={bar.value * UNIT}
            fill="currentColor"
            opacity="0.65"
          />
          <text
            x={BAR_GAP / 2 + i * slot + slot / 2}
            y={PAD + plotH + 15}
            textAnchor="middle"
            fontSize={LABEL_PX}
            fill="currentColor"
          >
            {bar.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

/** The sentence, from the same numbers the squares are drawn from. */
function describe(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return `A rectangle split into ${plural(figure.rows, 'row')} of ` +
             `${plural(figure.columns, 'equal square')}.`
    case 'bar_chart':
      // Every bar and its height, because the question asks the reader to
      // compare them -- a summary like "a bar chart of four categories" is
      // not the same information, and a screen-reader user would have a
      // different question from a sighted one.
      return 'A bar graph showing ' +
             figure.bars.map(b => `${b.label}: ${b.value}`).join(', ') + '.'
    default:
      return null
  }
}

function draw(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return <RectGrid rows={figure.rows} columns={figure.columns} />
    case 'bar_chart':
      return <BarGraph bars={figure.bars} />
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
  if (figure.type === 'bar_chart') {
    const bars = figure.bars
    if (!Array.isArray(bars) || bars.length < 2 || bars.length > MAX_BARS) return false
    return bars.every(b => b && typeof b.label === 'string' && b.label !== '' &&
      Number.isInteger(b.value) && b.value >= 1 && b.value <= MAX_BAR)
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
