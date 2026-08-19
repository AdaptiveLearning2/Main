import { motion } from 'framer-motion'

/** The headline figure on a dashboard: a label, a number, a tinted icon.
 *
 * The student and teacher dashboards each carried a copy of this, identical
 * but for one line -- the hover overlay's gradient, which is the only thing
 * either of them wanted different. That is the shape of duplication worth
 * removing: two files that must be edited together, where nothing says so, and
 * where the difference between them is a single token.
 *
 * `color` and `hoverTint` are raw Tailwind class strings rather than named
 * tones, matching how the call sites already wrote them. A name map would be
 * tidier and is the wrong trade here: these cards use per-card gradients
 * (`from-indigo-500 to-indigo-600`, `from-green-500 to-emerald-600`, ...) that
 * a fixed palette would have to enumerate, and a missing entry renders an
 * unstyled chip rather than failing.
 *
 * **`value ?? '—'` is load-bearing.** These render "not loaded" as an em dash,
 * which is why the dashboards can pass `null` for a figure whose read failed
 * instead of a zero -- the difference between a student who answered nothing
 * and a request that did not come back. Do not turn it into `value || '—'`: 0
 * is a real figure a student can have.
 *
 * Not to be confused with the compact tile in `pages/teacher/Students.jsx`,
 * which shares nothing but the idea -- different props, no motion, a third the
 * size, and it lives inside a table row rather than a dashboard grid.
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
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-1">{title}</p>
          <p className="text-3xl font-black text-gray-900 dark:text-white">{value ?? '—'}</p>
          {sub && <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2.5 ${color} rounded-xl shadow-md`}>
          <Icon size={20} className="text-white" />
        </div>
      </div>
    </motion.div>
  )
}
