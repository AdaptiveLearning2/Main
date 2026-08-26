import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { HelpCircle, Search, Filter, X, ChevronDown } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { fetchQuestionsCached } from '../../lib/questionsCache'
import { apiFetch } from '../../lib/api'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'
import useDialog from '../../hooks/useDialog'

const TOPICS = ['all','ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
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

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <motion.div
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
        <div className="space-y-2 mb-5">
          {question.options?.map((opt, i) => (
            <div key={i}
              className={`flex items-center gap-3 p-3 rounded-xl text-sm border ${i === question.correct_index ? 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200' : 'border-gray-100 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>
              <span className="w-6 h-6 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-xs font-bold text-gray-600 dark:text-gray-300">
                {String.fromCharCode(65 + i)}
              </span>
              <span>{opt}</span>
              {i === question.correct_index && <span className="ml-auto text-green-500 text-base">✓</span>}
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-600 dark:text-gray-400">ID: {question.id}</p>
      </motion.div>
    </motion.div>
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
  const [failed, setFailed]       = useState(false)
  const [page, setPage]           = useState(1)
  // Class then student, mirroring Sessions.jsx: there is no "all my students"
  // endpoint, rosters are per class, and fanning out across every class to
  // build one list is the read pattern CLAUDE.md warns about.
  const [classes, setClasses]     = useState([])
  const [classId, setClassId]     = useState('')
  const [roster, setRoster]       = useState({ classId: '', kids: [] })
  const [studentId, setStudentId] = useState('')
  // Only set when viewing one student: the per-student payload carries counts
  // the bank has no equivalent of, and the three-state read of "no questions"
  // needs `expired` to tell an empty history from one that aged out.
  const [studentMeta, setStudentMeta] = useState(null)
  const PER_PAGE = 15

  // loading already starts true, so no setState is needed here on mount.
  const load = () => {
    // Fetches the whole bank since this page paginates client-side.
    fetchQuestionsCached(1000)
      .then(q => { setQuestions(q || []); setStudentMeta(null); setFailed(false); setLoading(false) })
      .catch(e => { console.error('Failed to load questions:', e); setFailed(true); setLoading(false) })
  }

  // Deliberately not cached like the bank is: this is per student, changes as
  // they answer, and `fetchQuestionsCached`'s 30s window is tuned for content
  // that is the same for every teacher.
  const loadStudent = (id) => {
    apiFetch(`/api/students/${id}/questions?limit=200`)
      .then(res => {
        setQuestions(res.questions || [])
        setStudentMeta({
          answersRead: res.answers_read,
          expired: res.expired_questions,
          truncated: res.truncated,
        })
        setFailed(false); setLoading(false)
      })
      .catch(e => { console.error('Failed to load student questions:', e); setFailed(true); setLoading(false) })
  }

  const retry = () => { setLoading(true); studentId ? loadStudent(studentId) : load() }

  useEffect(() => { studentId ? loadStudent(studentId) : load() }, [studentId])

  useEffect(() => {
    // Failing to load classes costs the student filter, not the bank -- the
    // page's primary content does not depend on it, so this only logs.
    apiFetch('/api/classes')
      .then(rows => setClasses(rows || []))
      .catch(e => console.error('Failed to load classes:', e))
  }, [])

  useEffect(() => {
    if (!classId) return
    let cancelled = false
    apiFetch(`/api/classes/${classId}/students`)
      // Stored with the class it belongs to, so a response landing after the
      // selection moved on cannot be rendered under the new class's name --
      // the same superseded-read problem Sessions.jsx documents.
      .then(kids => { if (!cancelled) setRoster({ classId, kids: kids || [] }) })
      .catch(e => { if (!cancelled) { console.error('Failed to load roster:', e); setRoster({ classId, kids: [] }) } })
    return () => { cancelled = true }
  }, [classId])

  // Derived rather than reset in an effect: clearing it there is a setState
  // in an effect body, which this project keeps `react-hooks/set-state-in-
  // effect` clean of. Keying on the class the rows were fetched for also
  // means "All classes" and a class still loading both show nothing without
  // needing a separate loading flag.
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
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <HelpCircle className="text-violet-600" size={28} /> Question Bank
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">
          {loading ? '...'
            : studentMeta
              // "asked" not "total": this is one student's history, and the
              // count is of distinct questions, not of the bank.
              ? `${questions.length} question${questions.length === 1 ? '' : 's'} asked`
                + (studentMeta.expired ? ` · ${studentMeta.expired} no longer in the bank` : '')
                + (studentMeta.truncated ? ' · showing the most recent 200 answers' : '')
              : `${questions.length} questions total`}
        </p>
      </motion.div>

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
            {TOPICS.map(t => <option key={t} value={t}>{t === 'all' ? 'All Topics' : t.replace('_', ' ')}</option>)}
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
              {visibleRoster.map(s => (
                <option key={s.id} value={s.id}>{s.display_name || s.email || s.id}</option>
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
        // Distinct from "No questions found" so a failed load doesn't look like a filter problem.
        <LoadError what="the question bank" onRetry={retry} />
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
              // The bank returns `id`; the per-student payload returns
              // `question_id`. Both are the questions table's id.
              <motion.button key={q.id || q.question_id}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.025 }}
                whileHover={{ x: 3 }}
                // The student payload deliberately carries no `options` (the
                // endpoint does not send answer keys), so the modal has
                // nothing to show -- that view links to the session review
                // instead, where the answer is shown in context.
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
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 rounded-full capitalize">{q.subject.replace('_', ' ')}</span>
                    )}
                    {q.difficulty && (
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full capitalize ${DIFF_STYLE[q.difficulty] || ''}`}>{q.difficulty}</span>
                    )}
                    {q.attempts != null && (
                      // Rendered off `attempts`, not off studentMeta: a row
                      // either carries its own counts or it does not.
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-full">
                        {q.correct}/{q.attempts} correct
                      </span>
                    )}
                  </div>
                </div>
                <span className="text-xs text-gray-600 dark:text-gray-400 flex-shrink-0 pt-0.5">→</span>
              </motion.button>
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