import { motion } from 'framer-motion'

/**
 * A dashboard headline figure. `color`/`hoverTint` are complete Tailwind class strings.
 * `value ?? '—'`, never `||`: null is "not loaded", 0 is a real figure.
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
