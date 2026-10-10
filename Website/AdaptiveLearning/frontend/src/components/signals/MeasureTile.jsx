import { useId } from 'react'
import { Activity } from 'lucide-react'
import InfoTip from '../ui/InfoTip'
import { MEASURES, usualRange, usualWords } from '../../lib/signalGlossary'
import { valueOrReason } from '../../lib/signalFormat'

// Full class strings: Tailwind ships only classes it can see written out.
const TONES = {
  indigo: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
  emerald: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  teal: 'bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  rose: 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
  amber: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  sky: 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
}

/**
 * One measure: its glossary name with an info tip, the value or why there is none, and how
 * it compares with the student's own usual. `reason` is an `offLabel` input or a plain string.
 * `usual` is the backend's `usual.measures[key]`; absent (an older payload) draws no comparison.
 */
export default function MeasureTile({ measure, value, reason, usual, icon: Icon = Activity,
                                      tone = 'indigo', children }) {
  const labelId = useId()
  const m = MEASURES[measure]
  const shown = typeof reason === 'string'
    ? ((value && value !== 'N/A') ? value : reason)
    : valueOrReason(value, reason)
  const words = usualWords(usual)
  const range = usualRange(measure, usual)
  return (
    <div role="group" aria-labelledby={labelId}
         className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2">
            <p id={labelId} className="text-xs font-bold uppercase tracking-widest text-gray-600 dark:text-gray-400">
              {m.name}
            </p>
            <InfoTip label={m.name}>
              {m.what} Higher means: {m.higher.toLowerCase()}.{m.caveat ? ` ${m.caveat}` : ''}
            </InfoTip>
          </div>
          <p className="mt-1 text-2xl font-black text-gray-900 dark:text-white">{shown}</p>
          {words && <p className="text-sm font-bold text-gray-900 dark:text-white">{words}</p>}
          {range && <p className="text-xs text-gray-600 dark:text-gray-400">{range}</p>}
          {children}
        </div>
        <div className={`p-2.5 rounded-xl shrink-0 ${TONES[tone] || TONES.indigo}`}>
          <Icon size={18} aria-hidden="true" />
        </div>
      </div>
    </div>
  )
}
