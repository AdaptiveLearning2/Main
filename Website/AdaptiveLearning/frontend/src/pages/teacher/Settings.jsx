import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Settings } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { apiFetch } from '../../lib/api'
import { supabase } from '../../lib/supabase'

// "Notifications" is gone rather than fixed. It offered three switches -- a new
// student enrolling, a weekly report every Monday, alerts on generation
// failures -- and none of them had anything behind them: there is no push
// infrastructure in this product, no service worker, no VAPID key and no
// scheduled fan-out. The switches held React state and were forgotten on
// navigation.
//
// The same call was already made once, for the student's `practice_reminders`:
// it stopped describing notifications and became what it actually is, a banner.
// A switch that persists nothing is worse than an absent feature, because it
// tells a teacher a thing is on.
const TABS = ['General', 'Security', 'Appearance']

export default function TeacherSettings() {
  const { user, signOut }       = useAuth()
  const { dark, toggleTheme }   = useTheme()
  const navigate                = useNavigate()
  const [tab, setTab]           = useState('General')

  // `null` until the profile lands, so the field never shows a value the
  // teacher did not set. It used to be seeded from the email prefix, which
  // meant "Save Changes" on an untouched form would have written that guess
  // over a real display name -- if it had written anything at all.
  const [displayName, setDisplayName] = useState(null)
  const [savingName, setSavingName]   = useState(false)

  const [pw, setPw]         = useState({ current: '', next: '', confirm: '' })
  const [savingPw, setSavingPw] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/profile/me')
      .then(p => { if (!cancelled) setDisplayName(p?.display_name || '') })
      .catch(() => { if (!cancelled) setDisplayName('') })
    return () => { cancelled = true }
  }, [])

  const handleSignOut = async () => {
    await signOut()
    navigate('/login')
  }

  const saveName = async () => {
    setSavingName(true)
    try {
      await apiFetch('/api/profile/me', {
        method: 'PUT',
        body: { display_name: displayName.trim() },
      })
      toast.success('Saved.')
    } catch (e) {
      console.error('[settings] display name not saved', e)
      toast.error('That could not be saved.')
    } finally {
      setSavingName(false)
    }
  }

  const changePassword = async () => {
    if (pw.next !== pw.confirm)  return toast.error('The new passwords do not match.')
    if (pw.next.length < 6)      return toast.error('Use at least 6 characters.')

    setSavingPw(true)
    try {
      // The current password is *checked*, not decoration. Supabase's
      // `updateUser` does not ask for it, so a form that collects one and never
      // verifies it is claiming a protection it does not provide -- and on a
      // school machine left signed in, that protection is the point: without it
      // anyone passing the desk can take the account.
      const { error: reauth } = await supabase.auth.signInWithPassword({
        email: user?.email,
        password: pw.current,
      })
      if (reauth) {
        toast.error('That current password is not right.')
        return
      }

      const { error } = await supabase.auth.updateUser({ password: pw.next })
      if (error) throw error

      setPw({ current: '', next: '', confirm: '' })
      toast.success('Password updated.')
    } catch (e) {
      console.error('[settings] password not changed', e)
      toast.error(e?.message || 'The password could not be changed.')
    } finally {
      setSavingPw(false)
    }
  }

  return (
    <div className="p-6 lg:p-8 pb-12 max-w-4xl">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <Settings className="text-violet-600" size={28} /> Settings
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Manage your teacher account.</p>
      </motion.div>

      {/* tab bar */}
      <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-xl p-1 mb-6 overflow-x-auto">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-bold whitespace-nowrap transition ${tab === t ? 'bg-white dark:bg-gray-900 text-violet-600 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
            {t}
          </button>
        ))}
      </div>

      <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>

        {tab === 'General' && (
          <div className="space-y-4">
            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
              <h3 className="font-black text-gray-900 dark:text-white mb-5">Account Information</h3>
              <div className="flex items-center gap-4 mb-6">
                <div className="w-16 h-16 bg-gradient-to-br from-violet-500 to-purple-600 rounded-2xl flex items-center justify-center text-white text-2xl font-black shadow-lg">
                  {user?.email?.[0]?.toUpperCase()}
                </div>
                <div>
                  <p className="font-black text-gray-900 dark:text-white">{user?.email?.split('@')[0]}</p>
                  <p className="text-sm text-gray-500 dark:text-gray-400">{user?.email}</p>
                  <span className="text-xs font-bold text-violet-600 bg-violet-100 dark:bg-violet-900/30 px-2 py-0.5 rounded-full mt-1 inline-block">📚 Teacher</span>
                </div>
              </div>
              <div className="space-y-3">
                <div>
                  <label htmlFor="display-name" className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">Display Name</label>
                  <input id="display-name" value={displayName ?? ''} disabled={displayName === null}
                    onChange={e => setDisplayName(e.target.value)}
                    placeholder={displayName === null ? 'Loading…' : 'How your name appears to students'}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition disabled:opacity-50" />
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">Email</label>
                  <input defaultValue={user?.email} disabled
                    className="w-full px-4 py-2.5 bg-gray-100 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl text-sm text-gray-400 cursor-not-allowed" />
                </div>
                <button onClick={saveName} disabled={savingName || displayName === null}
                  className="px-5 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-xl text-sm font-bold transition shadow disabled:opacity-50">
                  {savingName ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </div>

            <div className="bg-white dark:bg-gray-900 rounded-2xl border border-rose-100 dark:border-rose-900/50 p-6 shadow-sm">
              <h3 className="font-black text-rose-600 mb-2">Danger Zone</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">Sign out of all sessions.</p>
              <button onClick={handleSignOut}
                className="px-5 py-2.5 bg-rose-600 hover:bg-rose-700 text-white rounded-xl text-sm font-bold transition shadow">
                Sign Out
              </button>
            </div>
          </div>
        )}

        {tab === 'Security' && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-4">
            <h3 className="font-black text-gray-900 dark:text-white">Change Password</h3>
            {/* Controlled, and submitted. These were three uncontrolled inputs
                next to a button that only raised a toast: a teacher could type
                a new password, be told it had been updated, and still be on the
                old one -- which is worse than the button not existing, because
                they would not try again. */}
            {[
              { key: 'current', label: 'Current password', auto: 'current-password' },
              { key: 'next',    label: 'New password',     auto: 'new-password' },
              { key: 'confirm', label: 'Confirm new password', auto: 'new-password' },
            ].map(f => (
              <div key={f.key}>
                <label htmlFor={`pw-${f.key}`} className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">{f.label}</label>
                <input id={`pw-${f.key}`} type="password" placeholder="••••••••"
                  autoComplete={f.auto}
                  value={pw[f.key]}
                  onChange={e => setPw(p => ({ ...p, [f.key]: e.target.value }))}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition" />
              </div>
            ))}
            <button onClick={changePassword}
              disabled={savingPw || !pw.current || !pw.next || !pw.confirm}
              className="px-5 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-xl text-sm font-bold transition shadow disabled:opacity-50">
              {savingPw ? 'Updating…' : 'Update Password'}
            </button>
          </div>
        )}

        {tab === 'Appearance' && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
            <h3 className="font-black text-gray-900 dark:text-white mb-5">Theme</h3>
            <div className="grid grid-cols-2 gap-3 max-w-xs">
              {[
                { id: 'light', label: 'Light', icon: '☀️' },
                { id: 'dark',  label: 'Dark',  icon: '🌙' },
              ].map(t => (
                <button key={t.id} onClick={() => { if ((t.id === 'dark') !== dark) toggleTheme() }}
                  className={`p-4 rounded-xl border-2 text-center transition-all ${(t.id === 'dark') === dark ? 'border-violet-500 bg-violet-50 dark:bg-violet-900/30' : 'border-gray-200 dark:border-gray-700 hover:border-violet-300'}`}>
                  <div className="text-2xl mb-1">{t.icon}</div>
                  <p className="text-sm font-bold text-gray-900 dark:text-white">{t.label}</p>
                </button>
              ))}
            </div>
          </div>
        )}

      </motion.div>
    </div>
  )
}