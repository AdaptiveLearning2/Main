import { isMixedScale } from '../../lib/scoreScale'

// A series measured on two score scales is not one series.
//
// When a rollup-backed payload's `score_scale` range straddles the change to
// the headband's scoring scale (see lib/scoreScale.js), the numbers on either
// side are not comparable and the chart cannot show where the step is, so it
// has to be said in words. Renders nothing for a single scale or for a payload
// predating the label: there is nothing true to add, and a permanent caption
// teaches readers to skip it.
export default function ScaleNote({ scale, what = 'These focus and stress figures' }) {
  if (!isMixedScale(scale)) return null
  return (
    <p role="note" className="text-xs text-amber-700 dark:text-amber-400 mb-2">
      {what} span a change in how the headband's scores are scaled, so values
      from before and after it are not comparable.
    </p>
  )
}
