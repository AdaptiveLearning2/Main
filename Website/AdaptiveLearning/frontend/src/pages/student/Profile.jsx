import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Copy, Check, Save } from 'lucide-react'
import ConsentChannels from '../../components/consent/ConsentChannels'
import Toggle from '../../components/ui/Toggle'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import { fetchSessionList } from '../../lib/session'
import { toast } from 'sonner'

// The three learning preferences, read off a profile the backend returned.
// One shared definition, used both on load and after a save.
//
// Use `??`, not `||`: 0 (adaptive bias) and false (reminders off) are valid
// choices, and `||` would overwrite them with the defaults every time.
const prefsFrom = (p) => ({
  difficulty_bias:          p?.difficulty_bias ?? 0,
  session_duration_minutes: p?.session_duration_minutes ?? 15,
  practice_reminders:       p?.practice_reminders ?? true,
})

// How often a code on screen is re-read, to notice it was used or expired.
const CODE_RECHECK_MS = 15_000

const TABS  = ['Overview', 'Account', 'Preferences', 'Devices']
const GRADES = ['1st Grade','2nd Grade','3rd Grade','4th Grade','5th Grade','6th Grade','7th Grade','8th Grade','Highschool','College']

export default function Profile() {
  const { user, displayName, refreshProfile, signOut } = useAuth()
  const [tab, setTab]       = useState('Overview')
  const [stats, setStats]   = useState(null)
  // The backend's count of every session, never the length of a list it sent:
  // that list is capped, so its length is a claim that the student did less.
  // `null` for a failed read *and* for a count that did not come back -- the
  // tile has nothing true to say in either case.
  const [sessionTotal, setSessionTotal] = useState(null)
  const [copied, setCopied] = useState(false)
  const [profile, setProfile] = useState(null)
  const [editName, setEditName] = useState('')
  const [editGrade, setEditGrade] = useState('')
  const [saving, setSaving] = useState(false)

  // Preferences live on the profile, not in localStorage, so they follow the
  // student to any device. Stays `null` until the profile loads, so the
  // controls don't render defaults as if the student had chosen them.
  const [prefs, setPrefs] = useState(null)
  const [prefsBusy, setPrefsBusy] = useState(false)
  // `null` until the read lands, then a `{code, expires_at}` or `null` for
  // "none outstanding". `codeRetrieved` keeps a failed read apart from that --
  // offering to create one on a failed read would replace a code the student
  // may have just read out to someone.
  const [linkCode, setLinkCode] = useState(null)
  const [codeRetrieved, setCodeRetrieved] = useState(null)
  const [codeBusy, setCodeBusy] = useState(false)

  useEffect(() => {
    Promise.all([
      apiFetch('/api/stats/me')
        .then(s => (s?.retrieved === false ? null : s))
        .catch(() => null),
      // One row: the page shows only the count, and the count comes back
      // beside the rows whatever the limit.
      fetchSessionList({ limit: 1 }).then(r => r.total).catch(() => null),
      apiFetch('/api/profile/me').catch(() => null),
      // `retrieved: false` rides on the payload, so a rejected promise and a
      // read that failed server-side arrive as the same thing here.
      apiFetch('/api/student/link-code').catch(() => ({ retrieved: false })),
    ]).then(([s, total, p, lc]) => {
      setStats(s)
      setSessionTotal(total)
      setProfile(p)
      setEditName(p?.display_name || '')
      setEditGrade(p?.grade_level || '')
      if (p) setPrefs(prefsFrom(p))
      setCodeRetrieved(lc?.retrieved !== false)
      setLinkCode(lc?.code ? { code: lc.code, expires_at: lc.expires_at } : null)
    })
  }, [])

  // A code on screen stops working without this page doing anything: a parent
  // redeems it, or it expires. So while one is showing it is re-read, or the
  // card goes on offering a spent code as live.
  const shownCode = linkCode?.code
  useEffect(() => {
    if (!shownCode) return
    const recheck = () => apiFetch('/api/student/link-code')
      .then(lc => {
        // A failed read says nothing about the code; leave it standing.
        if (lc?.retrieved === false) return
        // Only while it is still the code this read was about: a "New code"
        // that landed meanwhile is newer than this answer.
        setLinkCode(cur => (cur?.code !== shownCode ? cur
          : lc?.code ? { code: lc.code, expires_at: lc.expires_at } : null))
      })
      .catch(() => {})
    const timer = setInterval(recheck, CODE_RECHECK_MS)
    window.addEventListener('focus', recheck)
    return () => { clearInterval(timer); window.removeEventListener('focus', recheck) }
  }, [shownCode])

  // Update the UI immediately so taps feel instant, then reconcile with what
  // the server actually stored (it clamps values). Revert on failure so the
  // UI never shows a setting that didn't actually save.
  const savePrefs = async (updated) => {
    const previous = prefs
    setPrefs(updated)
    setPrefsBusy(true)
    try {
      const saved = await apiFetch('/api/profile/me', { method: 'PUT', body: updated })
      setProfile(saved)
      setPrefs(prefsFrom(saved))
    } catch (e) {
      setPrefs(previous)
      toast.error(e.message || 'Could not save that setting')
    } finally {
      setPrefsBusy(false)
    }
  }

  const saveProfile = async () => {
    setSaving(true)
    try {
      const updated = await apiFetch('/api/profile/me', {
        method: 'PUT',
        body: { display_name: editName.trim() || null, grade_level: editGrade || null }
      })
      setProfile(updated)
      refreshProfile()
      toast.success('Profile saved')
    } catch (e) {
      toast.error(e.message || 'Could not save profile')
    } finally {
      setSaving(false)
    }
  }

  const copyCode = () => {
    navigator.clipboard.writeText(linkCode?.code || '')
    setCopied(true)
    toast.success('Code copied!')
    setTimeout(() => setCopied(false), 2000)
  }

  const createCode = async () => {
    setCodeBusy(true)
    try {
      const res = await apiFetch('/api/student/link-code', { method: 'POST' })
      setLinkCode(res)
      // The read succeeded by definition now, so an earlier failed one must
      // stop suppressing the code we are holding.
      setCodeRetrieved(true)
    } catch (e) {
      toast.error(e.message || 'Could not create a code')
    } finally {
      setCodeBusy(false)
    }
  }

  const acc      = stats?.total_questions > 0 ? Math.round((stats.total_correct / stats.total_questions) * 100) : 0
  const initials = (profile?.display_name || displayName || user?.email || '?')[0].toUpperCase()
  const joined   = user?.created_at ? new Date(user.created_at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) : 'Unknown'



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
              {profile?.display_name || displayName || 'Student'}
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

            {/* This block used to show the user id, under "share this with a
                parent to link accounts". That was the instruction rather than
                a leak -- but the id is on every roster payload a teacher of
                this student reads and in the URL of every report page about
                them, so it was never the shared secret the sentence implied.
                A code has to be made here to exist, lasts 30 minutes and
                works once. */}
            <div className="mt-3 bg-white/10 rounded-xl p-3">
              <p className="text-xs text-indigo-200 mb-1">Link a parent</p>
              {codeRetrieved === false ? (
                // A failed read is not "you have no code": offering to create
                // one would replace a code the student may have just read out.
                <p className="text-xs text-indigo-100">
                  Couldn't check for a code just now.
                </p>
              ) : linkCode ? (
                <>
                  <p className="font-mono text-lg font-black tracking-widest text-white">{linkCode.code}</p>
                  <p className="text-[10px] text-indigo-300 mt-0.5">
                    Works once, until {new Date(linkCode.expires_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
                  </p>
                  <div className="flex items-center justify-center gap-3 mt-2">
                    <button onClick={copyCode}
                      className="flex items-center gap-1.5 text-xs font-bold text-indigo-200 hover:text-white transition">
                      {copied ? <><Check size={12} /> Copied!</> : <><Copy size={12} /> Copy</>}
                    </button>
                    <button onClick={createCode} disabled={codeBusy}
                      className="text-xs font-bold text-indigo-200 hover:text-white transition disabled:opacity-50">
                      New code
                    </button>
                  </div>
                </>
              ) : (
                <button onClick={createCode} disabled={codeBusy}
                  className="mt-1 w-full py-2 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold transition disabled:opacity-50">
                  {codeBusy ? 'Creating…' : 'Create a link code'}
                </button>
              )}
              <p className="text-[10px] text-indigo-300 mt-2">
                Read the code to a parent so they can see your progress. A new
                one replaces the old.
              </p>
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
                    // Show an em dash if the read failed, but 0 if it succeeded
                    // with no data -- don't report a network error as "you did nothing".
                    { label: 'Total Sessions',     value: sessionTotal ?? '—',                           icon: '📋' },
                    { label: 'Questions Answered', value: stats ? stats.total_questions ?? 0 : '—',      icon: '📝' },
                    { label: 'Correct Answers',    value: stats ? stats.total_correct ?? 0 : '—',        icon: '✅' },
                    { label: 'Best Streak',        value: stats ? stats.best_streak ?? 0 : '—',          icon: '🔥' },
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
                  <p className="text-xs text-gray-600 mt-2 dark:text-gray-400">
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
                  <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">This is what teachers and the leaderboard see.</p>
                </div>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Default Grade Level</label>
                  <select value={editGrade} onChange={e => setEditGrade(e.target.value)}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-indigo-500">
                    <option value="">— not set —</option>
                    {GRADES.map(g => <option key={g} value={g}>{g}</option>)}
                  </select>
                  <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">Used in Solo mode. Class mode uses the class's grade instead.</p>
                </div>

                <button onClick={saveProfile} disabled={saving}
                  className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-sm disabled:opacity-60 transition shadow">
                  <Save size={15} /> {saving ? 'Saving…' : 'Save changes'}
                </button>
              </div>
            )}

            {/* PREFERENCES */}
            {tab === 'Preferences' && !prefs && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
                {/* Show a loading message instead of default controls, so a tap
                    here can't save a setting the student never chose. */}
                <p className="text-sm text-gray-600 dark:text-gray-400">Loading your preferences…</p>
              </div>
            )}
            {tab === 'Preferences' && prefs && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-6">
                <h3 className="font-black text-gray-900 dark:text-white">Learning Preferences</h3>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Difficulty</label>
                  {/* Sets the starting Easier/Auto/Harder value on the practice page.
                      It's a shift on top of the model's own difficulty pick, so
                      there's no "medium" option -- medium and adaptive both mean
                      no shift. */}
                  <div className="grid grid-cols-3 gap-2">
                    {[[-1, 'Easier'], [0, 'Adaptive'], [1, 'Harder']].map(([v, label]) => (
                      <button key={v} disabled={prefsBusy}
                        onClick={() => savePrefs({ ...prefs, difficulty_bias: v })}
                        className={`py-2 rounded-xl text-sm font-semibold transition border disabled:opacity-60 ${prefs.difficulty_bias === v ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
                        {label}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">
                    Where each session starts. You can still change it while you practise — and
                    if the sensors show you are struggling, questions get easier whatever this says.
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Session Duration</label>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {[15, 30, 45, 60].map(d => (
                      <button key={d} disabled={prefsBusy}
                        onClick={() => savePrefs({ ...prefs, session_duration_minutes: d })}
                        className={`py-2 rounded-xl text-sm font-semibold transition border disabled:opacity-60 ${prefs.session_duration_minutes === d ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
                        {d} min
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-gray-600 mt-1 dark:text-gray-400">
                    You are asked when you reach it, between questions. Nothing stops on its own.
                  </p>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    {/* Named for what it actually is: a dashboard banner, not a
                        push notification -- there's no service worker or
                        scheduled fan-out behind it. */}
                    <p className="text-sm font-bold text-gray-700 dark:text-gray-300">Practice reminder</p>
                    <p className="text-xs text-gray-600 dark:text-gray-400">Show a nudge on your dashboard when you have not practised today</p>
                  </div>
                  <Toggle checked={prefs.practice_reminders}
                          onChange={v => savePrefs({ ...prefs, practice_reminders: v })} />
                </div>
              </div>
            )}

            {/* DEVICES */}
            {tab === 'Devices' && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-4">
                <h3 className="font-black text-gray-900 dark:text-white">Sensors</h3>
                {/* Sensor consent switches live here, not on the practice screen,
                    so a student can just sit down and answer questions. */}
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Choose what gets measured while you practise. You can turn anything off at any time.
                </p>
                {user?.id && <ConsentChannels studentId={user.id} role="student" />}
              </div>
            )}

          </motion.div>
        </motion.div>
      </div>
    </div>
  )
}