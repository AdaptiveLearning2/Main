import Heatmap from '../charts/Heatmap'
import Panel from './Panel'

/**
 * Which topics a class is struggling with, per student.
 *
 * Reads `/api/classes/{id}/topic-heatmap`, which reshapes
 * `user_math_performance` — the same counts the adaptive engine picks the next
 * question from, so a cell here and the difficulty a student is served cannot
 * disagree.
 *
 * `cells` arrives aligned to `topics` and is passed through in that order. The
 * server builds both from one pass for exactly this reason; re-sorting either
 * one here would reintroduce the drift the alignment exists to prevent.
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
          // The class figure under each heading, so a column that is red all
          // the way down reads as a teaching problem rather than as several
          // unrelated students having a bad week.
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
