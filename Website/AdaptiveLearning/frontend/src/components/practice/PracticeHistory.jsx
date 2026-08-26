/** A Quizlet-style "recent sets" list of the student's own past practice
 * sessions -- deliberately not merged into Dashboard's Topic Accuracy panel,
 * which is wired to live-session `user_math_performance`. Keeping the two
 * lists visibly separate is the point: practice tracking must read as its
 * own thing, not as more live-session history.
 */
export default function PracticeHistory({ sessions }) {
  if (!sessions || sessions.length === 0) return null
  return (
    <div className="mt-10">
      <h3 className="text-sm font-bold text-gray-500 dark:text-gray-400 mb-3 uppercase tracking-wide">
        Recent practice
      </h3>
      <div className="space-y-2">
        {sessions.slice(0, 5).map(s => (
          <div key={s.id}
            className="flex items-center justify-between bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 px-4 py-3 text-sm">
            <div className="min-w-0">
              <span className="font-bold capitalize text-gray-800 dark:text-gray-100">{s.mode}</span>
              <span className="text-gray-600 dark:text-gray-400 truncate">
                {' · '}{(s.topics || []).map(t => t.replace(/_/g, ' ')).join(', ')}
              </span>
            </div>
            <div className="text-gray-500 dark:text-gray-400 flex-shrink-0 ml-3">
              {!s.ended_at
                ? 'In progress'
                : s.mode === 'test' && s.questions_answered
                  ? `${Math.round((s.correct_answers / s.questions_answered) * 100)}%`
                  : `${s.questions_answered} card${s.questions_answered === 1 ? '' : 's'}`}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
