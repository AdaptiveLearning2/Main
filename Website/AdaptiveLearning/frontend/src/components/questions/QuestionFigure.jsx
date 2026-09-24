/**
 * The picture a question carries, drawn from its spec (`question_figures.py`).
 * Drawing and description come from the same object, so they cannot disagree.
 * An unknown or out-of-bounds spec renders nothing rather than throwing.
 * `role="img"` prunes children, so the label carries the whole description.
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

const MAX_PARTS = 8          // matches question_figures.MAX_PARTS
// Fixed whole width that the parts divide, so a whole is always the same size.
const WHOLE_W = 240
const PART_H = 56

// Kindergarten pictures. Keys match question_figures.FIGURE_ITEMS; the test pins them.
const ITEM_EMOJI = {
  apple: '🍎', star: '⭐', fish: '🐟', bird: '🐦', ball: '⚽', flower: '🌸',
  car: '🚗', duck: '🦆', cookie: '🍪', balloon: '🎈', frog: '🐸', cupcake: '🧁',
}
const MAX_OBJECTS = 20       // matches question_figures.MAX_OBJECTS
const MAX_GROUPS = 2         // matches question_figures.MAX_GROUPS
const SHAPE_SIDES = { circle: 0, triangle: 3, square: 4, rectangle: 4, hexagon: 6 }
const FRAME_CELL = 30
const SHAPE_BOX = 120

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
          // `currentColor` so the figure follows the theme.
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

// `BarGraph`, not the Recharts-style name: `AccessibleChart.test.jsx` matches
// that word anywhere in the file, comments included.
function BarGraph({ bars }) {
  const tallest = Math.max(...bars.map(b => b.value))
  const plotH = tallest * UNIT
  // Column as wide as the widest label, so labels never overlap.
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
      {/* Gridlines at every unit, so bars can be counted (1.MD.4). */}
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

function PartWhole({ parts, shaded }) {
  const partW = WHOLE_W / parts
  const width = WHOLE_W + PAD * 2
  const height = PART_H + PAD * 2
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="max-w-full h-auto text-gray-700 dark:text-gray-300"
      focusable="false"
      aria-hidden="true"
    >
      {Array.from({ length: parts }, (_, i) => (
        <rect
          key={i}
          x={PAD + i * partW}
          y={PAD}
          width={partW}
          height={PART_H}
          // Filled vs outlined, not two colours, so it survives black and white.
          fill={i < shaded ? 'currentColor' : 'none'}
          fillOpacity={i < shaded ? 0.65 : 0}
          stroke="currentColor"
          strokeWidth="1.5"
        />
      ))}
    </svg>
  )
}

function itemPlural(item) {
  return item === 'fish' ? item : `${item}s`
}

function chunks(n, size) {
  return Array.from({ length: Math.ceil(n / size) }, (_, i) => Math.min(size, n - i * size))
}

// Rows of five, so a group can be counted the way a ten frame is (K.CC.5).
function Objects({ groups }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4 text-3xl leading-none">
      {groups.map(group => (
        <div key={group.item} className="flex flex-col gap-2">
          {chunks(group.count, 5).map((size, row) => (
            <div key={row} className="flex gap-2">
              {Array.from({ length: size }, (_, i) => (
                <span key={i}>{ITEM_EMOJI[group.item]}</span>
              ))}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function TenFrames({ count }) {
  const frames = chunks(count, 10)
  const frameH = 2 * FRAME_CELL
  const width = 5 * FRAME_CELL + PAD * 2
  const height = frames.length * (frameH + 10) - 10 + PAD * 2
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="max-w-full h-auto text-gray-700 dark:text-gray-300"
      focusable="false"
      aria-hidden="true"
    >
      {frames.map((filled, f) => Array.from({ length: 10 }, (_, i) => {
        const x = PAD + (i % 5) * FRAME_CELL
        const y = PAD + f * (frameH + 10) + Math.floor(i / 5) * FRAME_CELL
        return (
          <g key={`${f}-${i}`}>
            <rect x={x} y={y} width={FRAME_CELL} height={FRAME_CELL}
              fill="none" stroke="currentColor" strokeWidth="1.5" />
            {i < filled && (
              <circle cx={x + FRAME_CELL / 2} cy={y + FRAME_CELL / 2} r={FRAME_CELL / 3}
                fill="currentColor" />
            )}
          </g>
        )
      }))}
    </svg>
  )
}

function shapePoints(shape) {
  const c = SHAPE_BOX / 2
  const r = SHAPE_BOX / 2 - PAD * 4
  if (shape === 'square') return [[c - r, c - r], [c + r, c - r], [c + r, c + r], [c - r, c + r]]
  if (shape === 'rectangle') {
    const h = r * 0.55
    return [[PAD * 2, c - h], [SHAPE_BOX - PAD * 2, c - h], [SHAPE_BOX - PAD * 2, c + h], [PAD * 2, c + h]]
  }
  const sides = SHAPE_SIDES[shape]
  // Flat base: rotate so no vertex points straight down.
  const start = -Math.PI / 2 + (sides % 2 ? 0 : Math.PI / sides)
  return Array.from({ length: sides }, (_, i) => {
    const angle = start + (2 * Math.PI * i) / sides
    return [c + r * Math.cos(angle), c + r * Math.sin(angle)]
  })
}

function Shape({ shape }) {
  const c = SHAPE_BOX / 2
  return (
    <svg
      viewBox={`0 0 ${SHAPE_BOX} ${SHAPE_BOX}`}
      width={SHAPE_BOX}
      height={SHAPE_BOX}
      className="max-w-full h-auto text-gray-700 dark:text-gray-300"
      focusable="false"
      aria-hidden="true"
    >
      {shape === 'circle'
        ? <circle cx={c} cy={c} r={c - PAD * 4} fill="currentColor" fillOpacity="0.15"
            stroke="currentColor" strokeWidth="2" />
        : <polygon points={shapePoints(shape).map(p => p.join(',')).join(' ')}
            fill="currentColor" fillOpacity="0.15" stroke="currentColor" strokeWidth="2" />}
    </svg>
  )
}

/** The sentence, from the same numbers the squares are drawn from. */
function describe(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return `A rectangle split into ${plural(figure.rows, 'row')} of ` +
             `${plural(figure.columns, 'equal square')}.`
    case 'part_whole':
      // Counts, not the fraction, which would give away the answer.
      return `A shape split into ${plural(figure.parts, 'equal part')}, ` +
             `${figure.shaded} of them shaded.`
    case 'bar_chart':
      // Every bar and its height: the question asks the reader to compare them.
      return 'A bar graph showing ' +
             figure.bars.map(b => `${b.label}: ${b.value}`).join(', ') + '.'
    case 'objects':
      // Each picture named once, so a listener counts them as a looker does.
      return 'A picture of ' + figure.groups.map(g =>
        `${itemPlural(g.item)}: ${Array(g.count).fill(g.item).join(', ')}`).join('. ') + '.'
    case 'ten_frames': {
      const frames = chunks(figure.count, 10)
      if (frames.length === 1) return `A ten frame with ${plural(figure.count, 'dot')}.`
      return `Two ten frames: the first full with 10 dots, the second with ${plural(frames[1], 'dot')}.`
    }
    case 'shape': {
      // Unnamed when the question asks for the name.
      if (figure.named) return `A picture of a ${figure.shape}.`
      const sides = SHAPE_SIDES[figure.shape]
      return sides
        ? `A picture of a flat shape with ${plural(sides, 'straight side')}.`
        : 'A picture of a round flat shape with no straight sides.'
    }
    default:
      return null
  }
}

function draw(figure) {
  switch (figure.type) {
    case 'rect_grid':
      return <RectGrid rows={figure.rows} columns={figure.columns} />
    case 'part_whole':
      return <PartWhole parts={figure.parts} shaded={figure.shaded} />
    case 'bar_chart':
      return <BarGraph bars={figure.bars} />
    case 'objects':
      return <Objects groups={figure.groups} />
    case 'ten_frames':
      return <TenFrames count={figure.count} />
    case 'shape':
      return <Shape shape={figure.shape} />
    default:
      return null
  }
}

function usable(figure) {
  if (!figure || typeof figure !== 'object') return false
  if (figure.type === 'rect_grid') {
    // Re-checked here: a bank row outlives the code that wrote it.
    return [figure.rows, figure.columns].every(
      n => Number.isInteger(n) && n >= 1 && n <= MAX_SIDE)
  }
  if (figure.type === 'part_whole') {
    const { parts, shaded } = figure
    if (!Number.isInteger(parts) || parts < 2 || parts > MAX_PARTS) return false
    return Number.isInteger(shaded) && shaded >= 1 && shaded < parts
  }
  if (figure.type === 'bar_chart') {
    const bars = figure.bars
    if (!Array.isArray(bars) || bars.length < 2 || bars.length > MAX_BARS) return false
    return bars.every(b => b && typeof b.label === 'string' && b.label !== '' &&
      Number.isInteger(b.value) && b.value >= 1 && b.value <= MAX_BAR)
  }
  if (figure.type === 'objects') {
    const groups = figure.groups
    if (!Array.isArray(groups) || groups.length < 1 || groups.length > MAX_GROUPS) return false
    if (new Set(groups.map(g => g && g.item)).size !== groups.length) return false
    return groups.every(g => g && Object.hasOwn(ITEM_EMOJI, g.item) &&
      Number.isInteger(g.count) && g.count >= 1 && g.count <= MAX_OBJECTS)
  }
  if (figure.type === 'ten_frames') {
    return Number.isInteger(figure.count) && figure.count >= 1 && figure.count <= MAX_OBJECTS
  }
  if (figure.type === 'shape') {
    return Object.hasOwn(SHAPE_SIDES, figure.shape) && typeof figure.named === 'boolean'
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
