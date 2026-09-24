import { describeScaleChange } from '../../lib/scoreScale'

// Says in words when a `score_scale` range mixes scales (see lib/scoreScale.js).
// Renders nothing for a single or unlabelled scale.
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
