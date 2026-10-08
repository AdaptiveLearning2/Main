import { useEffect, useRef, useState } from 'react'
import { m } from 'framer-motion'
import { apiFetch } from '../../lib/api'
import { fetchSessionList } from '../../lib/session'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'
import { stagger } from '../../lib/stagger'

// A figure nobody could establish. Not 0, which is a claim about the student.
const UNKNOWN = '—'

// A figure still on its way; distinct from UNKNOWN so the dash never flashes.
const PENDING = (
  <span role="status" aria-label="Loading"
        className="inline-block w-10 h-6 align-middle rounded bg-gray-200 dark:bg-gray-700 animate-pulse" />
)

const numberOr = v => (typeof v === 'number' ? v : null)

// `GET /api/practice-sessions` answers at most this many, newest first, with no total.
const PRACTICE_SHOWN = 20

export default function History() {
  const [sessions, setSessions] = useState([])
  // Real session count, not `sessions.length` past the cap; `null` if uncounted.
  const [total, setTotal]       = useState(null)
  const [truncated, setTruncated] = useState(null)
  // Lifetime totals, not a sum of the rows (a page of a longer history).
  // `undefined` in flight, `null` failed.
  const [stats, setStats]       = useState(undefined)
  const [loading, setLoading]   = useState(true)
  const [failed, setFailed]     = useState(false)
  // Practice sessions, a separate table: `undefined` in flight, `null` failed (not "none").
  const [practice, setPractice] = useState(undefined)
  const [filter, setFilter]     = useState('all')

  // Only the newest run writes, so a slow earlier failure can't undo a retry.
  const run = useRef(0)
  const load = () => {
    const mine = ++run.current
    const current = () => mine === run.current
    apiFetch('/api/stats/me')
      // No body is a failed read too, not "still loading".
      .then(s => { if (current()) setStats(s && s.retrieved !== false ? s : null) })
      .catch(() => { if (current()) setStats(null) })
    apiFetch('/api/practice-sessions')
      .then(rows => { if (current()) setPractice(Array.isArray(rows) ? rows : null) })
      .catch(e => { if (current()) { console.error('Failed to load practice sessions:', e); setPractice(null) } })
    fetchSessionList()
      .then(r => {
        if (!current()) return
        setSessions(r.sessions)
        setTotal(r.total)
        setTruncated(r.truncated)
        setFailed(false); setLoading(false)
      })
      // Set failed, or an empty list reads as "no sessions".
      .catch(e => {
        if (!current()) return
        console.error('Failed to load sessions:', e); setFailed(true); setLoading(false)
      })
  }

  // Reset here, not in `load`, which the mount effect also runs (set-state-in-effect).
  const retry = () => { setLoading(true); setStats(undefined); setPractice(undefined); load() }

  useEffect(load, [])

  // Both kinds, newest first; practice joins once its read lands, and a failed one is said below.
  const rows = [
    ...sessions.map(s => ({ ...s, kind: 'adaptive' })),
    ...(practice || []).map(p => ({ ...p, kind: 'practice' })),
  ].sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))

  // Past the backend's cut (`abandoned`) is neither complete nor in progress; null (unread) claims neither.
  const filtered = rows.filter(s => {
    if (filter === 'complete')   return !!s.ended_at
    if (filter === 'inprogress') return !s.ended_at && !s.abandoned && s.abandoned !== null
    return true
  })

  // A missing field, or accuracy of zero questions, is a dash, not 0.
  const totalQ   = numberOr(stats?.total_questions)
  const totalC   = numberOr(stats?.total_correct)
  const overallA = totalQ > 0 && totalC !== null
    ? `${Math.round((totalC / totalQ) * 100)}%` : null
  const statTile = v => (stats === undefined ? PENDING : v ?? UNKNOWN)

  return (
    <div className="p-6 lg:p-8 pb-12">
      <m.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Session History</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Your adaptive lessons and practice sessions.</p>
      </m.div>

      {sessions.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-4">
          {[
            // The backend's count, never the length of the capped list. Adaptive only: practice is not
            // credited to the lifetime totals.
            { label: 'Adaptive Sessions', value: total ?? UNKNOWN,   icon: '📋' },
            { label: 'Adaptive Questions', value: statTile(totalQ),   icon: '📝' },
            { label: 'Adaptive Accuracy',  value: statTile(overallA), icon: '🎯' },
          ].map((c, i) => (
            <m.div key={c.label}
              initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: stagger(i, 0.08) }}
              className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-4 shadow-sm text-center"
            >
              <div className="text-2xl mb-1">{c.icon}</div>
              <div className="text-xl font-black text-gray-900 dark:text-white">{c.value}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{c.label}</div>
            </m.div>
          ))}
        </div>
      )}

      {/* State the cap; only on `true`, since `null` means unknown. */}
      {truncated === true && (
        <p className="text-xs text-gray-600 dark:text-gray-400 mb-4">
          Showing your {sessions.length} most recent adaptive sessions
          {typeof total === 'number' ? ` of ${total}` : ''}.
        </p>
      )}
      {/* The practice list is capped at 20 with no count, so a full page can only say "up to". */}
      {practice?.length >= PRACTICE_SHOWN && (
        <p className="text-xs text-gray-600 dark:text-gray-400 mb-4">
          Showing your {PRACTICE_SHOWN} most recent practice sessions.
        </p>
      )}
      {!loading && !failed && practice === null && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-300 mb-4">
          Couldn't load your practice sessions, so only adaptive lessons are listed.{' '}
          <button onClick={retry} className="underline font-semibold">Try again</button>
        </p>
      )}

      <div className="flex gap-2 mb-5">
        {['all','complete','inprogress'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-4 py-1.5 rounded-full text-sm font-semibold transition capitalize ${filter === f ? 'bg-indigo-600 text-white shadow' : 'bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
            {f === 'inprogress' ? 'In Progress' : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {loading ? (
        <SkeletonList count={4} height="h-20" />
      ) : failed ? (
        <LoadError what="your session history" onRetry={retry} />
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">📭</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No sessions here</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm">Start a practice session to see it here.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((s, i) => {
            // Flashcards are ungraded: `questions_answered` counts cards seen and `correct_answers` stays 0.
            const flashcards = s.kind === 'practice' && s.mode === 'flashcard'
            // No answers is no accuracy, never a 0% (rule 2).
            const acc  = s.questions_answered > 0 ? Math.round((s.correct_answers / s.questions_answered) * 100) : null
            const done = !!s.ended_at
            const title = s.kind === 'practice'
              ? `Practice · ${flashcards ? 'Flashcards' : 'Test'}`
              : s.title || 'Adaptive Session'
            return (
              <m.div key={`${s.kind}:${s.id}`}
                initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: stagger(i, 0.04) }}
                whileHover={{ x: 4 }}
                className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm flex items-center justify-between hover:shadow-md transition-shadow"
              >
                <div className="flex items-center gap-4">
                  <div className={`w-11 h-11 rounded-xl flex items-center justify-center text-lg flex-shrink-0 ${done ? 'bg-green-50 dark:bg-green-900/30' : s.abandoned || s.abandoned === null ? 'bg-slate-100 dark:bg-gray-800' : 'bg-amber-50 dark:bg-amber-900/30'}`}>
                    {done ? '✅' : s.abandoned ? '⏹️' : s.abandoned === null ? '❔' : '⏳'}
                  </div>
                  <div>
                    <p className="font-bold text-gray-900 dark:text-white">{title}</p>
                    <p className="text-xs text-gray-600 mt-0.5 dark:text-gray-400">
                      {new Date(s.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      {!done && s.abandoned && ' · Not finished'}
                      {!done && s.abandoned === null && ' · Status unavailable'}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-6 text-right">
                  <div className="hidden sm:block">
                    <p className="text-sm font-black text-gray-900 dark:text-white">{s.questions_answered}</p>
                    <p className="text-xs text-gray-600 dark:text-gray-400">{flashcards ? 'cards' : 'questions'}</p>
                  </div>
                  {!flashcards && (
                    <div className="hidden sm:block">
                      <p className="text-sm font-black text-gray-900 dark:text-white">{s.correct_answers}</p>
                      <p className="text-xs text-gray-600 dark:text-gray-400">correct</p>
                    </div>
                  )}
                  <div>
                    {flashcards || acc === null ? (
                      <p className="text-lg font-black text-gray-900 dark:text-white">{UNKNOWN}</p>
                    ) : (
                      <p className={`text-lg font-black ${acc >= 70 ? 'text-green-500' : acc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{acc}%</p>
                    )}
                    <p className="text-xs text-gray-600 dark:text-gray-400">{flashcards ? 'not graded' : 'accuracy'}</p>
                  </div>
                </div>
              </m.div>
            )
          })}
        </div>
      )}
    </div>
  )
}