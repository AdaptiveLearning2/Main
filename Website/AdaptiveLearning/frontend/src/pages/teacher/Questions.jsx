import { useEffect, useRef, useState } from 'react'
import { m, AnimatePresence } from 'framer-motion'
import { HelpCircle, Search, Filter, X, ChevronDown } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { fetchQuestionCount, fetchQuestionsCached } from '../../lib/questionsCache'
import { apiFetch } from '../../lib/api'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'
import useDialog from '../../hooks/useDialog'
import { useLatestRequest } from '../../hooks/useLatestRequest'
import QuestionFigure from '../../components/questions/QuestionFigure'
import CCSSBadge from '../../components/questions/CCSSBadge'
import { correctIndex, optionList } from '../../lib/answerKey'
import { TOPICS as ALL_TOPICS, topicLabel } from '../../lib/topics'
import { stagger } from '../../lib/stagger'

const TOPICS = ['all', ...ALL_TOPICS]
const DIFFS  = ['all','easy','medium','hard']

const DIFF_STYLE = {
  easy:   'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  hard:   'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
}

function QuestionModal({ question, onClose }) {
  // Escape to close, Tab trapped inside, focus returned to the row that opened it.
  const panel = useRef(null)
  useDialog(panel, onClose)
  // `correct_answer` is text; no table has an index to compare against.
  const options = optionList(question)
  const correct = correctIndex(question, options)

  return (
    <m.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <m.div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="question-modal-text"
        initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 200, damping: 20 }}
        className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl p-7 max-w-lg w-full border border-gray-100 dark:border-gray-800"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex gap-2 flex-wrap">
            {question.subject && (
              <span className="text-xs font-bold px-2.5 py-1 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full capitalize">{question.subject}</span>
            )}
            {question.difficulty && (
              <span className={`text-xs font-bold px-2.5 py-1 rounded-full capitalize ${DIFF_STYLE[question.difficulty] || ''}`}>{question.difficulty}</span>
            )}
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 hover:text-gray-700 dark:hover:text-gray-200 transition dark:text-gray-400">
            <X size={18} />
          </button>
        </div>
        <p id="question-modal-text" className="text-base font-semibold text-gray-900 dark:text-white mb-5 leading-relaxed">{question.question_text}</p>
        <QuestionFigure figure={question.figure} />
        <CCSSBadge standard={question.ccss_standard} />
        <div className="space-y-2 mb-5">
          {options.map((opt, i) => (
            <div key={i}
              className={`flex items-center gap-3 p-3 rounded-xl text-sm border ${i === correct ? 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200' : 'border-gray-100 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>
              <span className="w-6 h-6 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-xs font-bold text-gray-600 dark:text-gray-300">
                {String.fromCharCode(65 + i)}
              </span>
              <span>{opt}</span>
              {i === correct && <span className="ml-auto text-green-600 text-base">✓ <span className="sr-only">correct answer</span></span>}
            </div>
          ))}
        </div>
        {correct === -1 && options.length > 0 && (
          <p className="text-xs text-gray-600 dark:text-gray-400 mb-3">
            The stored answer matches none of these options.
          </p>
        )}
        <p className="text-xs text-gray-600 dark:text-gray-400">ID: {question.id}</p>
      </m.div>
    </m.div>
  )
}

export default function Questions() {
  const navigate = useNavigate()
  const [questions, setQuestions] = useState([])
  const [loading, setLoading]     = useState(true)
  const [search, setSearch]       = useState('')
  const [topicFilter, setTopicFilter] = useState('all')
  const [diffFilter, setDiffFilter]   = useState('all')
  const [selected, setSelected]   = useState(null)
  // The error itself, not a flag, so `LoadError` can tell a refusal from an outage.
  const [failed, setFailed]       = useState(false)
  const [page, setPage]           = useState(1)
  // Class then student: rosters are per class, with no "all my students" endpoint.
  const [classes, setClasses]     = useState([])
  const [classId, setClassId]     = useState('')
  const [roster, setRoster]       = useState({ classId: '', kids: [] })
  const [studentId, setStudentId] = useState('')
  // Set only for one student; `expired` tells an empty history from an aged-out one.
  const [studentMeta, setStudentMeta] = useState(null)
  // The exact count, or null if it could not be read.
  const [bankTotal, setBankTotal] = useState(null)
  const PER_PAGE = 15

  // One counter across BOTH loaders: a cached bank read can land under an
  // in-flight student read. A ref, not a cleanup flag, because `retry()` also calls.
  const beginQuestionRead = useLatestRequest()

  const load = () => {
    const isCurrent = beginQuestionRead()
    // The list is capped at 1000 rows, so its length is not the bank's size.
    fetchQuestionCount().then(setBankTotal)
    // The newest 1000: this page paginates client-side.
    fetchQuestionsCached(1000)
      .then(q => {
        if (!isCurrent()) return
        setQuestions(q || []); setStudentMeta(null); setFailed(false); setLoading(false)
      })
      // Guarded too, so a superseded failure can't override a later success.
      .catch(e => {
        if (!isCurrent()) return
        console.error('Failed to load questions:', e); setFailed(e); setLoading(false)
      })
  }

  // Not cached: per student and changes as they answer.
  const loadStudent = (id) => {
    const isCurrent = beginQuestionRead()
    apiFetch(`/api/students/${id}/questions?limit=200`)
      .then(res => {
        if (!isCurrent()) return
        setQuestions(res.questions || [])
        setStudentMeta({
          answersRead: res.answers_read,
          expired: res.expired_questions,
          truncated: res.truncated,
        })
        setFailed(false); setLoading(false)
      })
      .catch(e => {
        if (!isCurrent()) return
        console.error('Failed to load student questions:', e); setFailed(e); setLoading(false)
      })
  }

  const retry = () => { setLoading(true); studentId ? loadStudent(studentId) : load() }

  // The loaders read only the request counter and setters, so `studentId` is the whole dependency.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { studentId ? loadStudent(studentId) : load() }, [studentId])

  useEffect(() => {
    // Failure costs only the student filter, so this just logs.
    apiFetch('/api/classes', { cache: true })
      .then(rows => setClasses(rows || []))
      .catch(e => console.error('Failed to load classes:', e))
  }, [])

  useEffect(() => {
    if (!classId) return
    let cancelled = false
    apiFetch(`/api/classes/${classId}/students`)
      // Stored with its class, so a late response can't render under another.
      .then(kids => { if (!cancelled) setRoster({ classId, kids: kids || [] }) })
      .catch(e => { if (!cancelled) { console.error('Failed to load roster:', e); setRoster({ classId, kids: [] }) } })
    return () => { cancelled = true }
  }, [classId])

  // Derived, not reset in an effect; "All classes" and a loading class show nothing.
  const visibleRoster = roster.classId === classId ? roster.kids : []

  const filtered = questions.filter(q => {
    const matchSearch = !search || q.question_text?.toLowerCase().includes(search.toLowerCase())
    const matchTopic  = topicFilter === 'all' || q.subject === topicFilter
    const matchDiff   = diffFilter  === 'all' || q.difficulty === diffFilter
    return matchSearch && matchTopic && matchDiff
  })

  const totalPages = Math.ceil(filtered.length / PER_PAGE)
  const paginated  = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE)

  const resetFilters = () => { setSearch(''); setTopicFilter('all'); setDiffFilter('all'); setPage(1) }
  const hasFilters   = search || topicFilter !== 'all' || diffFilter !== 'all'

  return (
    <div className="p-6 lg:p-8 pb-12">
      <m.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <HelpCircle className="text-violet-600" size={28} /> Question Bank
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          {loading ? '...'
            : studentMeta
              // "asked", not "total": distinct questions in one student's history.
              ? `${questions.length} question${questions.length === 1 ? '' : 's'} asked`
                + (studentMeta.expired ? ` · ${studentMeta.expired} no longer in the bank` : '')
                + (studentMeta.truncated ? ' · showing the most recent 200 answers' : '')
              : bankTotal === null
                ? `${questions.length} questions loaded`
                : `${bankTotal} questions total`
                  + (bankTotal > questions.length ? ` · showing the newest ${questions.length}` : '')}
        </p>
      </m.div>

      <div className="flex flex-wrap gap-3 mb-6">
        <div className="relative">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-600 dark:text-gray-400" />
          <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }}
            className="pl-9 pr-4 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition w-48"
            placeholder="Search questions..." />
        </div>

        <div className="relative">
          <select value={topicFilter} onChange={e => { setTopicFilter(e.target.value); setPage(1) }}
            className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 capitalize cursor-pointer">
            {TOPICS.map(t => <option key={t} value={t}>{t === 'all' ? 'All Topics' : topicLabel(t)}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-600 pointer-events-none dark:text-gray-400" />
        </div>

        <div className="relative">
          <select value={diffFilter} onChange={e => { setDiffFilter(e.target.value); setPage(1) }}
            className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 capitalize cursor-pointer">
            {DIFFS.map(d => <option key={d} value={d}>{d === 'all' ? 'All Difficulties' : d}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-600 pointer-events-none dark:text-gray-400" />
        </div>

        {classes.length > 0 && (
          <div className="relative">
            <select value={classId} aria-label="Filter by class"
              onChange={e => { setClassId(e.target.value); setStudentId(''); setPage(1) }}
              className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 cursor-pointer">
              <option value="">All classes</option>
              {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-600 pointer-events-none dark:text-gray-400" />
          </div>
        )}

        {visibleRoster.length > 0 && (
          <div className="relative">
            <select value={studentId} aria-label="Filter by student"
              onChange={e => { setStudentId(e.target.value); setPage(1); setLoading(true) }}
              className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 cursor-pointer">
              <option value="">Whole bank</option>
              {/* Roster rows carry `user_id`/`name` (null when unset), not `id`/`display_name`. */}
              {visibleRoster.map(s => (
                <option key={s.user_id} value={s.user_id}>{s.name || s.email || 'No name set'}</option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-600 pointer-events-none dark:text-gray-400" />
          </div>
        )}

        {hasFilters && (
          <button onClick={resetFilters}
            className="flex items-center gap-1.5 px-3 py-2 bg-rose-50 dark:bg-rose-900/20 text-rose-500 rounded-xl text-sm font-semibold hover:bg-rose-100 dark:hover:bg-rose-900/40 transition">
            <X size={14} /> Clear
          </button>
        )}
      </div>

      {loading ? (
        <SkeletonList count={5} height="h-14" gap="space-y-2" />
      ) : failed ? (
        // Named for whichever read failed: the bank is public, so a refusal is
        // always about the student.
        <LoadError error={failed} onRetry={retry}
          what={studentId ? "this student's questions" : 'the question bank'} />
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-6xl mb-4">🔍</div>
          <h3 className="text-xl font-black text-gray-900 dark:text-white mb-2">No questions found</h3>
          <p className="text-gray-500 dark:text-gray-400 text-sm mb-4">Try adjusting your filters or search term.</p>
          {hasFilters && <button onClick={resetFilters} className="text-sm text-violet-600 font-bold hover:underline">Clear all filters</button>}
        </div>
      ) : (
        <>
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 shadow-sm overflow-hidden mb-4">
            {paginated.map((q, i) => (
              // Bank rows have `id`, per-student rows `question_id`; same id.
              <m.button key={q.id || q.question_id}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: stagger(i, 0.025) }}
                whileHover={{ x: 3 }}
                // The student payload has no `options`, so link to the session review.
                onClick={() => (studentMeta ? navigate(`/teacher/sessions/${q.session_id}`) : setSelected(q))}
                className="w-full flex items-start gap-4 px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors text-left"
              >
                <span className="text-xs font-black text-gray-600 w-7 flex-shrink-0 pt-0.5 dark:text-gray-400">
                  {(page - 1) * PER_PAGE + i + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-gray-900 dark:text-white line-clamp-1">{q.question_text}</p>
                  <div className="flex gap-2 mt-1.5">
                    {q.subject && (
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full capitalize">{topicLabel(q.subject)}</span>
                    )}
                    {q.difficulty && (
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full capitalize ${DIFF_STYLE[q.difficulty] || ''}`}>{q.difficulty}</span>
                    )}
                    {q.attempts != null && (
                      // Keyed on the row's own `attempts`, not studentMeta.
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-full">
                        {q.correct}/{q.attempts} correct
                      </span>
                    )}
                  </div>
                </div>
                <span className="text-xs text-gray-600 dark:text-gray-400 flex-shrink-0 pt-0.5">→</span>
              </m.button>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-700 text-sm font-semibold text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition">
                ← Prev
              </button>
              <span className="text-sm font-bold text-gray-700 dark:text-gray-300 px-2">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-700 text-sm font-semibold text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition">
                Next →
              </button>
            </div>
          )}
        </>
      )}

      <AnimatePresence>
        {selected && <QuestionModal question={selected} onClose={() => setSelected(null)} />}
      </AnimatePresence>
    </div>
  )
}