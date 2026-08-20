/**
 * Parent settings — the account, the children linked to it, and what is
 * measured for each.
 *
 * A student can withdraw consent at any time; only a linked parent can
 * re-enable it. Turning a channel back on raises `needs_student_ack`, so the
 * child is told on their next load instead of just noticing data reappear.
 */

import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ShieldCheck, User, Users, Unlink, Mail, CalendarDays, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { apiFetch } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import ConsentChannels from '../../components/consent/ConsentChannels'
import SkeletonList from '../../components/ui/Skeleton'
import LoadError from '../../components/ui/LoadError'

function fmtDate(s) {
  if (!s) return null
  const d = new Date(s)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' })
}

export default function ParentSettings() {
  const { user } = useAuth()

  const [children, setChildren] = useState(null)
  const [failed, setFailed]     = useState(false)

  // `null` until the profile lands, so the field never shows a value the
  // parent did not set.
  const [displayName, setDisplayName] = useState(null)
  const [savingName, setSavingName]   = useState(false)
  const [joinedAt, setJoinedAt]       = useState(null)

  // Which child's unlink is awaiting a second click, since unlinking removes
  // report access and shouldn't happen on a stray click.
  const [confirmingUnlink, setConfirmingUnlink] = useState(null)
  const [unlinking, setUnlinking]               = useState(null)

  const loadChildren = useCallback(() => {
    // include_face=false: this page shows names and consent switches only,
    // no facial data. Left at the default the endpoint would read
    // `face_signals` for every linked child.
    apiFetch('/api/parent/children?include_face=false')
      .then(c => { setChildren(c || []); setFailed(false) })
      .catch(e => { console.error('[parent settings] children not loaded', e); setFailed(true) })
  }, [])

  useEffect(() => { loadChildren() }, [loadChildren])

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/profile/me')
      .then(p => {
        if (cancelled) return
        setDisplayName(p?.display_name || '')
        setJoinedAt(p?.created_at || null)
      })
      .catch(() => { if (!cancelled) setDisplayName('') })
    return () => { cancelled = true }
  }, [])

  const saveName = async () => {
    setSavingName(true)
    try {
      await apiFetch('/api/profile/me', {
        method: 'PUT',
        body: { display_name: displayName.trim() },
      })
      toast.success('Saved.')
    } catch (e) {
      console.error('[parent settings] display name not saved', e)
      toast.error('That could not be saved.')
    } finally {
      setSavingName(false)
    }
  }

  const unlink = async (child) => {
    setUnlinking(child.user_id)
    try {
      await apiFetch(`/api/parent/children/${child.user_id}`, { method: 'DELETE' })
      setChildren(list => (list || []).filter(c => c.user_id !== child.user_id))
      setConfirmingUnlink(null)
      toast.success(`Unlinked from ${child.name || 'your child'}.`)
    } catch (e) {
      console.error('[parent settings] unlink failed', e)
      toast.error('That could not be unlinked.')
    } finally {
      setUnlinking(null)
    }
  }

  const joined = fmtDate(joinedAt)

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center gap-3 mb-1">
          <ShieldCheck className="text-indigo-600 dark:text-indigo-400" size={22} />
          <h1 className="text-2xl font-black text-gray-900 dark:text-white">Settings</h1>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          Your account, the children linked to it, and what is measured during
          their practice sessions.
        </p>

        {/* ── your account ─────────────────────────────────────────── */}
        <section className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm mb-6">
          <h2 className="font-black text-gray-900 dark:text-white flex items-center gap-2 mb-4">
            <User size={16} className="text-indigo-500" /> Your account
          </h2>

          <label htmlFor="parent-display-name"
                 className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">
            Display name
          </label>
          <input
            id="parent-display-name"
            value={displayName ?? ''}
            // Disabled until the profile lands, so a fast typist's input
            // can't be overwritten by the response arriving late.
            disabled={displayName === null}
            onChange={e => setDisplayName(e.target.value)}
            placeholder={displayName === null ? 'Loading…' : 'Your name'}
            className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition text-sm disabled:opacity-60"
          />
          <p className="text-xs text-gray-400 mt-1.5">
            What your children see when you turn a sensor back on.
          </p>

          <div className="mt-4 grid sm:grid-cols-2 gap-3 text-sm">
            <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
              <Mail size={14} className="flex-shrink-0" />
              {/* Read-only — changing it needs a verification flow this page
                  doesn't implement. Shown so a parent can confirm the
                  account they're signed in to. */}
              <span className="truncate">{user?.email || '—'}</span>
            </div>
            {joined && (
              <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
                <CalendarDays size={14} className="flex-shrink-0" />
                <span>Joined {joined}</span>
              </div>
            )}
          </div>

          <button onClick={saveName} disabled={savingName || displayName === null}
                  className="mt-5 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold disabled:opacity-50 transition">
            {savingName ? 'Saving…' : 'Save changes'}
          </button>
        </section>

        {/* ── your children ────────────────────────────────────────── */}
        <section className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm mb-6">
          <h2 className="font-black text-gray-900 dark:text-white flex items-center gap-2 mb-1">
            <Users size={16} className="text-indigo-500" /> Your children
          </h2>
          <p className="text-xs text-gray-400 mb-4">
            Unlinking removes your access to a child&apos;s reports and to their
            sensor settings. Nothing already recorded is deleted, and their own
            choices are unchanged.
          </p>

          {children === null && !failed && <SkeletonList count={2} height="h-14" gap="space-y-2" />}
          {failed && <LoadError what="your children" onRetry={loadChildren} />}

          {children?.length === 0 && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              No children linked yet.
            </p>
          )}

          <div className="space-y-2">
            {/* `user_id`, not `id` — matches what /api/parent/children returns. */}
            {children?.map(child => (
              <div key={child.user_id}
                   className="flex items-center justify-between gap-3 flex-wrap rounded-xl border border-gray-100 dark:border-gray-800 px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-bold text-gray-900 dark:text-white truncate">
                    {child.name || child.email || 'Child'}
                  </p>
                  {fmtDate(child.linked_at) && (
                    <p className="text-xs text-gray-400">Linked {fmtDate(child.linked_at)}</p>
                  )}
                </div>
                {confirmingUnlink === child.user_id ? (
                  <div className="flex items-center gap-2">
                    <button onClick={() => unlink(child)} disabled={unlinking === child.user_id}
                            className="px-3 py-2 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold disabled:opacity-50">
                      {unlinking === child.user_id ? 'Unlinking…' : 'Yes, unlink'}
                    </button>
                    <button onClick={() => setConfirmingUnlink(null)} disabled={unlinking === child.user_id}
                            className="px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-xs font-bold disabled:opacity-50">
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button onClick={() => setConfirmingUnlink(child.user_id)}
                          className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-rose-300 hover:text-rose-600 text-xs font-bold transition">
                    <Unlink size={13} /> Unlink
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Shown whether or not any children are linked, so the empty
              state has a way forward too. */}
          <Link to="/parent/link"
                className="mt-4 inline-flex items-center gap-1.5 px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-200 dark:border-gray-700 text-sm font-bold text-gray-600 dark:text-gray-300 hover:border-emerald-300 hover:text-emerald-600 transition">
            <Plus size={14} /> Link another child
          </Link>
        </section>

        {/* ── what is measured ─────────────────────────────────────── */}
        <h2 className="font-black text-gray-900 dark:text-white flex items-center gap-2 mb-1">
          <ShieldCheck size={16} className="text-indigo-500" /> What is measured
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          Off means it is never measured or saved — not hidden from reports.
        </p>

        {children?.length === 0 && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 text-center">
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Link a child first and their sensor settings will appear here.
            </p>
          </div>
        )}

        <div className="space-y-6">
          {children?.map(child => (
            <div key={child.user_id}
                 className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-4">
                {child.name || child.email || 'Child'}
              </h3>
              {/* `role="parent"`: switches are two-way with no confirmation
                  step, since a parent can reverse their own change here. */}
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
