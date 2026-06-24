import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Link as LinkIcon, Hash } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

export default function ParentLinkChild() {
  const [childId, setChildId] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!childId.trim()) return
    setLoading(true)
    try {
      const res = await apiFetch('/api/parent/link-child', {
        method: 'POST',
        body: { child_id: childId.trim() }
      })
      toast.success(`Linked to ${res.child_name}! 🎉`)
      navigate('/parent')
    } catch (err) {
      toast.error(err.message || 'Could not link child')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6 lg:p-8 pb-12 max-w-lg">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <LinkIcon className="text-emerald-600" size={28} /> Link a Child
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Connect your child's account to monitor their progress.</p>
      </motion.div>

      {/* instructions */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}
        className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-2xl p-5 mb-6">
        <h3 className="font-black text-emerald-800 dark:text-emerald-200 mb-2">How to get your child's User ID</h3>
        <ol className="space-y-2 text-sm text-emerald-700 dark:text-emerald-300">
          <li className="flex items-start gap-2"><span className="font-black">1.</span> Have your child log in to AdaptiveLearning</li>
          <li className="flex items-start gap-2"><span className="font-black">2.</span> They go to <strong>Profile → Overview</strong></li>
          <li className="flex items-start gap-2"><span className="font-black">3.</span> They copy their <strong>User ID</strong> shown there</li>
          <li className="flex items-start gap-2"><span className="font-black">4.</span> Paste it in the box below</li>
        </ol>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
        className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">Child's User ID</label>
            <div className="relative">
              <Hash size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={childId}
                onChange={e => setChildId(e.target.value)}
                className="w-full pl-10 pr-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-emerald-500 dark:text-white outline-none transition text-sm font-mono"
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                required
              />
            </div>
            <p className="text-xs text-gray-400 mt-1">This is a UUID — looks like: <span className="font-mono">a1b2c3d4-e5f6-...</span></p>
          </div>

          <motion.button type="submit" disabled={loading || !childId.trim()}
            whileHover={{ scale: 1.01 }} whileTap={{ scale: 0.98 }}
            className="w-full py-3 bg-gradient-to-r from-emerald-600 to-teal-600 text-white rounded-xl font-bold shadow-lg disabled:opacity-50 transition-all flex items-center justify-center gap-2">
            {loading
              ? <motion.div animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} className="w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
              : <><LinkIcon size={16} /><span>Link Child Account</span></>}
          </motion.button>
        </form>
      </motion.div>
    </div>
  )
}