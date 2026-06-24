import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'

export default function NotFound() {
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-gray-950 flex items-center justify-center p-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: 'spring', stiffness: 150 }}
        className="text-center"
      >
        <motion.div
          animate={{ y: [0, -12, 0] }}
          transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }}
          className="text-8xl mb-6"
        >
          🧠
        </motion.div>
        <h1 className="text-6xl font-black text-gray-900 dark:text-white mb-3">404</h1>
        <p className="text-xl font-semibold text-gray-600 dark:text-gray-400 mb-2">Page not found</p>
        <p className="text-gray-400 mb-8 text-sm">Let's get you back on track.</p>
        <Link to="/dashboard">
          <motion.span
            whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.97 }}
            className="inline-flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-violet-600 text-white px-8 py-3 rounded-xl font-bold text-sm shadow-lg"
          >
            ← Back to Dashboard
          </motion.span>
        </Link>
      </motion.div>
    </div>
  )
}