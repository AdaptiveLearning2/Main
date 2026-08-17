import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Copy, Check, Save } from 'lucide-react'
import ConsentChannels from '../../components/consent/ConsentChannels'
import { useAuth } from '../../context/AuthContext'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'

// The three learning preferences, read off a profile the backend returned.
//
// One definition, because there are two places that need it -- the initial load
// and the reconcile after a save -- and they were written out separately with
// the defaults repeated in each. Two copies of a default is how one of them
// ends up disagreeing with the column default it is standing in for.
//
// `??`, not `||`: 0 is the adaptive bias and false is reminders off, and both
// are choices a student made. `||` would quietly restore the default every time
// this ran, so turning reminders off would never stick.
const prefsFrom = (p) => ({
  difficulty_bias:          p?.difficulty_bias ?? 0,
  session_duration_minutes: p?.session_duration_minutes ?? 15,
  practice_reminders:       p?.practice_reminders ?? true,
})

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

  // Preferences live on the profile, not in localStorage.
  //
  // `al_prefs` was written here and read by nothing -- not the adaptive engine,
  // not the session, not any reminder -- so all three controls were decoration.
  // localStorage could not have fixed that on its own either: the backend picks
  // the difficulty and it cannot read a key in one browser's storage, and a
  // preference that does not survive the student opening the app on a school
  // computer is not a preference.
  //
  // `null` until the profile lands, which is what stops the controls rendering
  // a default as though the student had chosen it.
  const [prefs, setPrefs] = useState(null)
  const [prefsBusy, setPrefsBusy] = useState(false)
  const [sessionsFailed, setSessionsFailed] = useState(false)

  useEffect(() => {
    Promise.all([
      apiFetch('/api/stats/me')
        .then(s => (s?.retrieved === false ? null : s))
        .catch(() => null),
      // `null`, not `[]`. An empty array is a student who has never practised;
      // this is a request that did not come back, and the two drove the same
      // "no sessions" rendering below.
      apiFetch('/api/sessions').catch(() => null),
      apiFetch('/api/profile/me').catch(() => null),
    ]).then(([s, sess, p]) => {
      setStats(s)
      setSessionsFailed(sess === null)
      setSessions(sess || [])
      setProfile(p)
      setEditName(p?.display_name || '')
      setEditGrade(p?.grade_level || '')
      if (p) setPrefs(prefsFrom(p))
    })
  }, [])

  // Optimistic, then reconciled against what the server stored.
  //
  // Optimistic because these are three-tap controls and a round trip per tap
  // makes them feel broken; reconciled because the endpoint clamps, so what
  // came back is the authority. Reverted on failure rather than left showing a
  // setting that was not saved -- a preference silently not applying is the
  // exact failure this whole change is fixing.
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
      {/* `left-0` is load-bearing. Without it `left` resolves to the span's
          static position, and a button centres its content -- so the knob
          started at 22px, the middle of a 44px track, and the translate moved
          it from there. On read as 46px on a 44px track (18px outside the
          pill); off read as 26px, hard against the right end. Both states drew
          the knob to the right of centre, so the control could not be read at
          all -- and no state ever looked broken enough to be obviously a bug. */}
      <span className={`absolute left-0 top-1 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${value ? 'translate-x-6' : 'translate-x-1'}`} />
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
                    // Em dash where the read failed, zero where it succeeded
                    // and found nothing. A student's own profile is the last
                    // place to report a network error as "you did nothing".
                    { label: 'Total Sessions',     value: sessionsFailed ? '—' : sessions.length,        icon: '📋' },
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
            {tab === 'Preferences' && !prefs && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
                {/* Not the controls at their defaults. Rendering those before the
                    profile lands shows the student settings they did not choose,
                    and a tap during that window saves whatever was on screen. */}
                <p className="text-sm text-gray-400">Loading your preferences…</p>
              </div>
            )}
            {tab === 'Preferences' && prefs && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-6">
                <h3 className="font-black text-gray-900 dark:text-white">Learning Preferences</h3>

                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">Difficulty</label>
                  {/* Three options, not four, because three is what the system
                      has. This sets the starting value of the Easier/Auto/Harder
                      control on the practice page, which is a *shift* applied on
                      top of the difficulty the model picked from the student's
                      own accuracy history. There is no "always medium" to offer:
                      medium and adaptive would both mean no shift. */}
                  <div className="grid grid-cols-3 gap-2">
                    {[[-1, 'Easier'], [0, 'Adaptive'], [1, 'Harder']].map(([v, label]) => (
                      <button key={v} disabled={prefsBusy}
                        onClick={() => savePrefs({ ...prefs, difficulty_bias: v })}
                        className={`py-2 rounded-xl text-sm font-semibold transition border disabled:opacity-60 ${prefs.difficulty_bias === v ? 'bg-indigo-600 text-white border-indigo-600 shadow' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-indigo-300'}`}>
                        {label}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-gray-400 mt-1">
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
                  <p className="text-[11px] text-gray-400 mt-1">
                    You are asked when you reach it, between questions. Nothing stops on its own.
                  </p>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    {/* Named for what it does. "Notifications — daily reminders
                        to practice" described a system that does not exist: a
                        reminder that reaches a closed browser needs a service
                        worker, VAPID keys and a scheduled fan-out, and none of
                        that is here. This is a banner on the dashboard, so it
                        says so -- the same reason FacialRecognitionToggle was
                        retired rather than given a disclaimer. */}
                    <p className="text-sm font-bold text-gray-700 dark:text-gray-300">Practice reminder</p>
                    <p className="text-xs text-gray-400">Show a nudge on your dashboard when you have not practised today</p>
                  </div>
                  <Toggle value={prefs.practice_reminders}
                          onChange={v => savePrefs({ ...prefs, practice_reminders: v })} />
                </div>
              </div>
            )}

            {/* DEVICES */}
            {tab === 'Devices' && (
              <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-4">
                <h3 className="font-black text-gray-900 dark:text-white">Sensors</h3>
                {/* This tab used to list Muse Headband and Webcam with "Connect"
                    buttons that did nothing when clicked -- the only
                    sensor-related control a student could find, and decorative.
                    Real consent state is what they should have been showing.

                    The switches live here rather than on the practice screen on
                    purpose: a student sits down and answers questions, and
                    sensor settings are somewhere else, the way they are in any
                    normal app. */}
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