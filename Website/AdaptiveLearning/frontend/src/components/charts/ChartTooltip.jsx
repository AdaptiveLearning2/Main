import { Tooltip } from 'recharts'
import { useTheme } from '../../context/ThemeContext'
import { tooltipStyles } from './chartTooltipStyles'

/**
 * The only place a Recharts tooltip is rendered (enforced by ChartTooltip.test.jsx),
 * styled from the theme. No ThemeProvider (a test) reads as light.
 */
export default function ChartTooltip(props) {
  const dark = !!useTheme()?.dark
  return <Tooltip {...tooltipStyles(dark)} {...props} />
}
