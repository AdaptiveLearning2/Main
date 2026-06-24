import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { HelpCircle, Search, Filter, X, ChevronDown } from 'lucide-react'
import { apiFetch } from '../../lib/api'

const TOPICS = ['all','ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const DIFFS  = ['all','easy','medium','hard']

const DIFF_STYLE = {
  easy:   'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  hard:   'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
}

function QuestionModal({ question, onClose }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <motion.div
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
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition">
            <X size={18} />
          </button>
        </div>
        <p className="text-base font-semibold text-gray-900 dark:text-white mb-5 leading-relaxed">{question.question_text}</p>
        <div className="space-y-2 mb-5">
          {question.options?.map((opt, i) => (
            <div key={i}
              className={`flex items-center gap-3 p-3 rounded-xl text-sm border ${i === question.correct_index ? 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200' : 'border-gray-100 dark:border-gray-700 text-gray-600 dark:text-gray-300'}`}>
              <span className="w-6 h-6 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-xs font-bold text-gray-500 dark:text-gray-400">
                {String.fromCharCode(65 + i)}
              </span>
              <span>{opt}</span>
              {i === question.correct_index && <span className="ml-auto text-green-500 text-base">✓</span>}
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-400">ID: {question.id}</p>
      </motion.div>
    </motion.div>
  )
}

export default function Questions() {
  const [questions, setQuestions] = useState([])
  const [loading, setLoading]     = useState(true)
  const [search, setSearch]       = useState('')
  const [topicFilter, setTopicFilter] = useState('all')
  const [diffFilter, setDiffFilter]   = useState('all')
  const [selected, setSelected]   = useState(null)
  const [page, setPage]           = useState(1)
  const PER_PAGE = 15

  useEffect(() => {
    apiFetch('/api/questions?limit=1000')
      .then(q => { setQuestions(q || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

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
          {loading ? '...' : `${questions.length} questions total`}
        </p>
      </motion.div>

      {/* filters */}
      <div className="flex flex-wrap gap-3 mb-6">
        <div className="relative">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }}
            className="pl-9 pr-4 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition w-48"
            placeholder="Search questions..." />
        </div>

        <div className="relative">
          <select value={topicFilter} onChange={e => { setTopicFilter(e.target.value); setPage(1) }}
            className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 capitalize cursor-pointer">
            {TOPICS.map(t => <option key={t} value={t}>{t === 'all' ? 'All Topics' : t.replace('_', ' ')}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        </div>

        <div className="relative">
          <select value={diffFilter} onChange={e => { setDiffFilter(e.target.value); setPage(1) }}
            className="appearance-none pl-3 pr-8 py-2 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 capitalize cursor-pointer">
            {DIFFS.map(d => <option key={d} value={d}>{d === 'all' ? 'All Difficulties' : d}</option>)}
          </select>
          <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        </div>

        {hasFilters && (
          <button onClick={resetFilters}
            className="flex items-center gap-1.5 px-3 py-2 bg-rose-50 dark:bg-rose-900/20 text-rose-500 rounded-xl text-sm font-semibold hover:bg-rose-100 dark:hover:bg-rose-900/40 transition">
            <X size={14} /> Clear
          </button>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">{[1,2,3,4,5].map(i => <div key={i} className="h-14 bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 animate-pulse" />)}</div>
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
              <motion.button key={q.id}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.025 }}
                whileHover={{ x: 3 }}
                onClick={() => setSelected(q)}
                className="w-full flex items-start gap-4 px-5 py-4 border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-slate-50 dark:hover:bg-gray-800 transition-colors text-left"
              >
                <span className="text-xs font-black text-gray-400 w-7 flex-shrink-0 pt-0.5">
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
                  </div>
                </div>
                <span className="text-xs text-gray-300 dark:text-gray-600 flex-shrink-0 pt-0.5">→</span>
              </motion.button>
            ))}
          </div>

          {/* pagination */}
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

      {/* question detail modal */}
      <AnimatePresence>
        {selected && <QuestionModal question={selected} onClose={() => setSelected(null)} />}
      </AnimatePresence>
    </div>
  )
}