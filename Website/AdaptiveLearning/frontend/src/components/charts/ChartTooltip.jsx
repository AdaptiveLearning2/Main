import { Tooltip } from 'recharts'
import { useTheme } from '../../context/ThemeContext'
import { tooltipStyles } from './chartTooltipStyles'

/**
 * The one place a Recharts tooltip is rendered.
 *
 * Recharts' default tooltip is a white box whose text inherits the page
 * colour, so in dark mode the label rendered light grey on white -- the bucket
 * name on the focus-and-accuracy chart was unreadable, which is the one line
 * that says what the bar is. Styles are chosen from the theme rather than
 * left to inheritance, and every chart goes through this component so the
 * next chart cannot forget (ChartTooltip.test.jsx enforces it).
 *
 * `useTheme()` is undefined outside a ThemeProvider (a component test), which
 * reads as light -- the stock look, not a crash.
 */
export default function ChartTooltip(props) {
  const dark = !!useTheme()?.dark
  return <Tooltip {...tooltipStyles(dark)} {...props} />
}
