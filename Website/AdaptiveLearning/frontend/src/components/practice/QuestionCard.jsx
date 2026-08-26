import { motion } from 'framer-motion'
import { normalizeValue } from '../../lib/practiceQuestion'

const DIFFICULTY_TONE = {
  easy: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  hard: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
}
const MEDIUM_TONE = 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'

/**
 * @param question   a normalized question from `normalizeQuestion`
 * @param selected   index of the option the student picked, or null
 * @param revealed   whether to show correct/incorrect styling
 * @param onSelect   called with the picked index
 */
export default function QuestionCard({ question: q, selected, revealed, onSelect }) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 p-7">
      <div className="flex gap-2 mb-4 flex-wrap">
        {q.topic && (
          <span className="text-xs font-bold px-2.5 py-1 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 rounded-full capitalize">
            {String(q.topic).replace(/_/g, ' ')}
          </span>
        )}
        {q.difficulty && (
          <span className={`text-xs font-bold px-2.5 py-1 rounded-full capitalize ${DIFFICULTY_TONE[q.difficulty] || MEDIUM_TONE}`}>
            {q.difficulty}
          </span>
        )}
      </div>

      <p className="text-lg font-semibold text-gray-900 dark:text-white mb-6 leading-relaxed">{q.text}</p>

      <div className="space-y-3">
        {q.options.map((opt, i) => {
          const isCorrectOption = normalizeValue(opt) === normalizeValue(q.correctAnswer)
          let style = 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:border-indigo-300'
          if (revealed) {
            if (isCorrectOption) style = 'border-green-400 bg-green-50 dark:bg-green-900/30 text-green-800 dark:text-green-200'
            else if (i === selected) style = 'border-rose-400 bg-rose-50 dark:bg-rose-900/30 text-rose-800 dark:text-rose-200'
            else style = 'border-gray-100 dark:border-gray-700 opacity-50'
          } else if (selected === i) {
            style = 'border-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-200'
          }
          return (
            <motion.button key={i} onClick={() => onSelect(i)} disabled={revealed}
              whileHover={!revealed ? { x: 4 } : {}}
              className={`w-full text-left p-4 rounded-xl border-2 transition-all duration-200 flex items-center gap-3 ${style}`}
            >
              <span className="w-7 h-7 flex-shrink-0 rounded-lg bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 flex items-center justify-center text-sm font-bold text-gray-600 dark:text-gray-300">
                {String.fromCharCode(65 + i)}
              </span>
              <span>{Array.isArray(opt) ? opt.join(', ') : opt}</span>
              {revealed && isCorrectOption && (
                <span className="ml-auto text-green-500 text-lg">✓</span>
              )}
              {revealed && selected === i && !isCorrectOption && (
                <span className="ml-auto text-rose-500 text-lg">✗</span>
              )}
            </motion.button>
          )
        })}
      </div>
    </div>
  )
}
