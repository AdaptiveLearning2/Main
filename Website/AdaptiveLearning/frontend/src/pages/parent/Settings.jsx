/**
 * Parent settings — the only surface that can turn a channel back on.
 *
 * A student may withdraw at any time and that decision stands; only a linked
 * parent may re-enable. Without this page the "off" was effectively permanent,
 * which is not what the consent model says and not what a parent was told.
 *
 * Turning a channel back on raises `needs_student_ack`, so the child is told on
 * their next load. Discovering a resumed sensor by noticing data reappear is a
 * surprise, not consent.
 */

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { ShieldCheck } from 'lucide-react'
import { apiFetch } from '../../lib/api'
import ConsentChannels from '../../components/consent/ConsentChannels'

export default function ParentSettings() {
  const [children, setChildren] = useState(null)
  const [error, setError]       = useState(null)

  useEffect(() => {
    apiFetch('/api/parent/children')
      .then(c => setChildren(c || []))
      .catch(e => setError(String(e.message || e)))
  }, [])

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center gap-3 mb-1">
          <ShieldCheck className="text-indigo-600 dark:text-indigo-400" size={22} />
          <h1 className="text-2xl font-black text-gray-900 dark:text-white">Settings</h1>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          What is measured during practice sessions, for each child. Off means it
          is never measured or saved — not hidden from reports.
        </p>

        {error && <p className="text-sm text-rose-500">{error}</p>}
        {!children && !error && <p className="text-sm text-gray-400">Loading…</p>}

        {children?.length === 0 && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 text-center">
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No children linked yet. Link a child first and their sensor settings will appear here.
            </p>
          </div>
        )}

        <div className="space-y-6">
          {/* `user_id`, not `id` -- that is what /api/parent/children returns,
              and the same key ChildDetail routes on. */}
          {children?.map(child => (
            <div key={child.user_id}
                 className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
              <h2 className="font-black text-gray-900 dark:text-white mb-4">
                {child.name || child.email || 'Child'}
              </h2>
              {/* `role="parent"`, so the switches are two-way and no
                  confirmation step appears: a parent's change is reversible by
                  the same parent on the same screen. */}
              <ConsentChannels studentId={child.user_id} role="parent"
                               studentName={child.name || null} />
              <p className="text-xs text-gray-400 mt-4">
                Turning something back on tells {child.name || 'your child'} the
                next time they open the app.
              </p>
            </div>
          ))}
        </div>
      </motion.div>
    </div>
  )
}
