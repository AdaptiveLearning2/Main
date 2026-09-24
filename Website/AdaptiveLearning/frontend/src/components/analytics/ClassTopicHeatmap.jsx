import Heatmap from '../charts/Heatmap'
import Panel from './Panel'

/**
 * Per-student topic accuracy for a class, from `user_math_performance`.
 * `cells` arrives aligned to `topics` by the server; never re-sort either.
 */
export default function ClassTopicHeatmap({ data, loading, onRetry }) {
  const topics = data?.topics || []
  const students = data?.students || []

  return (
    <Panel
      title="Topic accuracy"
      note="Per student, across every topic this class has been served."
      loading={loading}
      failed={data?.retrieved === false}
      what="topic accuracy"
      onRetry={onRetry}
      empty={!topics.length}
      emptyNote="No topics answered yet. Cells appear as students work through them."
    >
      <Heatmap
        caption={`${students.length} students against ${topics.length} topics. Colour runs from red at 0% to green at 100%.`}
        rowHeader="Student"
        minAttempts={data?.min_attempts || 0}
        columns={topics.map(t => ({
          key: t.topic_id,
          label: t.topic_name,
          // The class figure under each heading.
          sublabel: typeof t.accuracy === 'number'
            ? `${Math.round(t.accuracy * 100)}% class`
            : 'no attempts',
        }))}
        rows={students.map(s => ({
          key: s.user_id,
          label: s.name,
          cells: s.cells,
        }))}
      />
    </Panel>
  )
}
