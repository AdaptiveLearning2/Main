import { motion } from 'framer-motion'

export default function PageLoader() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-slate-50 dark:bg-gray-950 gap-4">
      <motion.div
        animate={{ rotate: 360 }}
        transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        className="w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full"
      />
      <p className="text-sm text-gray-400 dark:text-gray-500 tracking-wide">Loading...</p>
    </div>
  )
}