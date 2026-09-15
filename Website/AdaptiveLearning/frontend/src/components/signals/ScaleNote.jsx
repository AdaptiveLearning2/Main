import { describeScaleChange } from '../../lib/scoreScale'

// A series measured on two score scales is not one series.
//
// When a rollup-backed payload's `score_scale` range straddles a change in
// the headband's scoring (see lib/scoreScale.js), the numbers on either side
// are not comparable and the chart cannot show where the split is, so it has
// to be said in words -- naming which figures it moves (the local calm source
// moves stress and leaves focus alone) and whether the split is a step in
// time or two sidecars scoring calm two ways at once. Renders nothing for a
// single scale or for a payload predating the label: there is nothing true to
// add, and a permanent caption teaches readers to skip it.
export default function ScaleNote({ scale, what = 'These figures' }) {
  const change = describeScaleChange(scale)
  if (!change) return null
  const parts = []
  if (change.versionStep) {
    parts.push(`span a change in how the headband's ${change.affected} scores are scaled,`
      + ' so values from before and after it are not comparable')
  }
  if (change.sourceSplit) {
    parts.push(`${change.versionStep ? 'and they ' : ''}mix stress scores from headbands`
      + ' scoring calm two different ways, so those stress values are not comparable'
      + ' with each other')
  }
  return (
    <p role="note" className="text-xs text-amber-700 dark:text-amber-400 mb-2">
      {what} {parts.join(', ')}.
    </p>
  )
}
