import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Copy, Check, Save } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

const TABS  = ['Overview', 'Account', 'Preferences', 'Devices']
const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

export default function Profile() {
  const { user, signOut } = useAuth()
  const [tab, setTab]       = useState('Overview')
  const [stats, setStats]   = useState(null)
  const [sessions, setSessions] = useState([])
  const [copied, setCopied] = useState(false)
  const [profile, setProfile] = useState(null)
  const [editName, setEditName] = useState('')
  const [editGrade, setEditGrade] = useState('')
  const [saving, setSaving] = useState(false)

  const [prefs, setPrefs] = useState(() => {
    const s = localStorage.getItem('al_prefs')
    return s ? JSON.parse(s) : { difficulty: 'adaptive', duration: '15', notifications: true }
  })

  useEffect(() => {
    Promise.all([
      apiFetch('/api/stats/me').catch(() => null),
      apiFetch('/api/sessions').catch(() => []),
      apiFetch('/api/profile/me').catch(() => null),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessions(sess || [])
      setProfile(p)
      setEditName(p?.display_name || '')
      setEditGrade(p?.grade_level || '')
    })
  }, [])

  const savePrefs = (updated) => {
    setPrefs(updated)
    localStorage.setItem('al_prefs', JSON.stringify(updated))
  }

  const saveProfile = async () => {
    setSaving(true)
    try {
      const updated = await apiFetch('/api/profile/me', {
        method: 'PUT',
        body: { display_name: editName.trim() || null, grade_level: editGrade || null }
      })
      setProfile(updated)
      toast.success('Profile saved')
    } catch (e) {
      toast.error(e.message || 'Could not save profile')
    } finally {
      setSaving(false)
    }
  }

  const copyId = () => {
    navigator.clipboard.writeText(user?.id || '')
    setCopied(true)
    toast.success('User ID copied!')
    setTimeout(() => setCopied(false), 2000)
  }

  const acc      = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0
  const initials = (profile?.display_name || user?.email || '?')[0].toUpperCase()
  const joined   = user?.created_at ? new Date(user.created_at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) : 'Unknown'

  const Toggle = ({ value, onChange }) => (
    <button onClick={() => onChange(!value)}
      className={`w-11 h-6 rounded-full transition-colors duration-200 relative flex-shrink-0 ${value ? 'bg-indigo-600' : 'bg-gray-300 dark:bg-gray-600'}`}>
      <span className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${value ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )

  return (
    <div className="p-6 lg:p-8 pb-12">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Your Profile</h1>
      </motion.div>

      <div className="grid lg:grid-cols-3 gap-6">

        {/* avatar / ID card */}
        <motion.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}>
          <div className="bg-gradient-to-br from-indigo-600 to-violet-700 rounded-2xl p-7 text-white text-center shadow-xl">
            <div className="w-20 h-20 bg-white/20 rounded-full flex items-center justify-center text-4xl font-black mx-auto mb-4">
              {initials}
            </div>
            <h2 className="text-xl font-black">
              {profile?.display_name || user?.email?.split('@')[0] || 'Student'}
            </h2>
            <p className="text-indigo-200 text-sm mt-1 break-all">{user?.email}</p>
            {profile?.grade_level && (
              <p className="mt-2 inline-block text-xs font-bold bg-white/20 px-2 py-1 rounded-full">
                🎓 {profile.grade_level}
              </p>
            )}

            <div className="mt-4 bg-white/10 rounded-xl p-3">
              <p className="text-xs text-indigo-200 mb-0.5">Member since</p>
              <p className="font-bold text-sm">{joined}</p>
            </div>

            <div className="mt-3 bg-white/10 rounded-xl p-3">
              <p className="text-xs text-indigo-200 mb-1">Your User ID</p>
              <p className="font-mono text-xs text-white break-all leading-relaxed">{user?.id}</p>
              <button onClick={copyId}
                className="mt-2 flex items-center gap-1.5 mx-auto text-xs font-bold text-indigo-200 hover:text-white transition">
                {copied ? <><Check size={12} /> Copied!</> : <><Copy size={12} /> Copy ID</>}
              </button>
              <p className="text-[10px] text-indigo-300 mt-2">Share this with a parent to link accounts</p>
            </div>

            <button onClick={signOut}
              className="mt-4 w-full py-2 bg-white/10 hover:bg-white/20 text-white rounded-xl text-sm font-semibold transition">
              🚪 Sign Out
            </button>
          </div>
        </motion.div>

        {/* tabs */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }} className="lg:col-span-2 space-y-4">
          <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-xl p-1 flex-wrap">
            {TABS.map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`flex-1 min-w-fit py-2 px-3 text-sm font-bold rounded-lg transition ${tab === t ? 'bg-white dark:bg-gray-900 text-indigo-600 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                {t}
              </button>
            ))}
          </div>

          <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>

            {/* OVERVIEW */}
            {tab === 'Overview' && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: 'Total Sessions',     value: sessions.length,             icon: '📋' },
                    { label: 'Questions Answered', value: stats?.total_questions ?? 0, icon: '📝' },
                    { label: 'Correct Answers',    value: stats?.total_correct ?? 0,   icon: '✅' },
                    { label: 'Best Streak',        value: stats?.best_streak ?? 0,     icon: '🔥' },
                  ].map(c => (
                    <div key={c.label} className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm text-center">
                      <div className="text-2xl mb-1">{c.icon}</div>
                      <div className="text-2xl font-black text-gray-900 dark:text-white">{c.value}</div>
                      <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{c.label}</div>
                    </div>
                  ))}
                </div>

                <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm">
                  <div className="flex justify-between items-center mb-3">
                    <span className="text-sm font-bold text-gray-700 dark:text-gray-300">Overall Accuracy</span>
                    <span className={`text-lg font-black ${acc >= 70 ? 'text-green-500' : acc >= 40 ? 'text-amber-500' : 'text-rose-500'}`}>{acc}%</span>
                  </div>
                  <div className="h-3 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                    <motion.div className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full"
                      initial={{ width: 0 }} animate={{ width: `${acc}%` }} transition={{ duration: 0.8 }} />
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    {acc >= 70 ? '🔥 Crushing it!' : acc >= 40 ? '👍 Solid work!' : '💪 Keep grinding!'}
                  </p>
                </div>
              </div>
            )}

            {/* ACCOUNT — name + grade */}
            {tab === 'Account' && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-5">
                <h3 className="font-black text-gray-900 dark:text-white">Your Info</h3>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Display Name</label>
                  <input value={editName} onChange={e => setEditName(e.target.value)}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                    placeholder="What should we call you?" />
                  <p className="text-[11px] text-gray-400 mt-1">This is what teachers and the leaderboard see.</p>
                </div>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Default Grade Level</label>
                  <select value={editGrade} onChange={e => setEditGrade(e.target.value)}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-indigo-500">
                    <option value="">— not set —</option>
                    {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
                  </select>
                  <p className="text-[11px] text-gray-400 mt-1">Used in Solo mode. Class mode uses the class's grade instead.</p>
                </div>

                <button onClick={saveProfile} disabled={saving}
                  className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-sm disabled:opacity-60 transition shadow">
                  <Save size={15} /> {saving ? 'Saving…' : 'Save changes'}
                </button>
              </div>
            )}

            {/* PREFERENCES */}
            {tab === 'Preferences' && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-6">
                <h3 className="font-black text-gray-900 dark:text-white">Learning Preferences</h3>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Difficulty Mode</label>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {['adaptive', 'easy', 'medium', 'hard'].map(d => (
                      <button key={d} onClick={() => savePrefs({ ...prefs, difficulty: d })}
                        className={`py-2 rounded-xl text-sm font-semibold capitalize transition border ${prefs.difficulty === d ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
                        {d}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Session Duration</label>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {['15', '30', '45', '60'].map(d => (
                      <button key={d} onClick={() => savePrefs({ ...prefs, duration: d })}
                        className={`py-2 rounded-xl text-sm font-semibold transition border ${prefs.duration === d ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
                        {d} min
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-bold text-gray-700 dark:text-gray-300">Notifications</p>
                    <p className="text-xs text-gray-400">Daily reminders to practice</p>
                  </div>
                  <Toggle value={prefs.notifications} onChange={v => savePrefs({ ...prefs, notifications: v })} />
                </div>
              </div>
            )}

            {/* DEVICES */}
            {tab === 'Devices' && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-4">
                <h3 className="font-black text-gray-900 dark:text-white">Device Integrations</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400">Connect cognitive monitoring devices for enhanced adaptive learning.</p>
                {[
                  { name: 'Muse Headband', desc: 'EEG signal monitoring — measures focus and stress in real time', icon: '🧠' },
                  { name: 'Webcam',        desc: 'Facial recognition — detects engagement and confusion',          icon: '📷' },
                ].map(d => (
                  <div key={d.name} className="flex items-start justify-between p-4 bg-slate-50 dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700">
                    <div className="flex items-start gap-3">
                      <span className="text-2xl">{d.icon}</span>
                      <div>
                        <p className="font-bold text-gray-900 dark:text-white text-sm">{d.name}</p>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 max-w-xs">{d.desc}</p>
                      </div>
                    </div>
                    <button className="flex-shrink-0 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold rounded-lg transition">Connect</button>
                  </div>
                ))}
              </div>
            )}

          </motion.div>
        </motion.div>
      </div>
    </div>
  )
}