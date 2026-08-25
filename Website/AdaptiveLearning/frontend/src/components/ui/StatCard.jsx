import { motion } from 'framer-motion'

/** The headline figure on a dashboard: a label, a number, a tinted icon.
 *
 * The student and teacher dashboards each carried a copy of this, identical
 * but for the hover overlay's gradient.
 *
 * `color` and `hoverTint` are raw Tailwind class strings rather than named
 * tones, matching how call sites already wrote them -- a fixed palette would
 * have to enumerate every per-card gradient and render an unstyled chip on
 * a miss.
 *
 * **`value ?? '—'` is load-bearing.** Renders "not loaded" as an em dash, so
 * callers can pass `null` for a failed read instead of a zero. Do not turn it
 * into `value || '—'`: 0 is a real figure a student can have.
 *
 * Not to be confused with the compact tile in `pages/teacher/Students.jsx`,
 * which shares nothing but the idea.
 */
export default function StatCard({
  icon: Icon,
  title,
  value,
  sub,
  color,
  hoverTint = 'from-indigo-400/10 to-violet-500/10',
  delay,
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      whileHover={{ y: -4, transition: { duration: 0.15 } }}
      className="relative group bg-white dark:bg-gray-900 rounded-2xl p-5 border border-gray-100 dark:border-gray-800 shadow-sm hover:shadow-lg transition-shadow"
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${hoverTint} rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300`} />
      <div className="relative flex items-start justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-600 dark:text-gray-400 mb-1">{title}</p>
          <p className="text-3xl font-black text-gray-900 dark:text-white">{value ?? '—'}</p>
          {sub && <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2.5 ${color} rounded-xl shadow-md`}>
          <Icon size={20} className="text-white" />
        </div>
      </div>
    </motion.div>
  )
}
