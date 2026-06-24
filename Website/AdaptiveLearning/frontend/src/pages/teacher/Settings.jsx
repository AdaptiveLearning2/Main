import { useState } from 'react'
import { motion } from 'framer-motion'
import { Settings } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

const TABS = ['General', 'Notifications', 'Security', 'Appearance']

function Toggle({ value, onChange }) {
  return (
    <button onClick={() => onChange(!value)}
      className={`w-11 h-6 rounded-full transition-colors duration-200 relative flex-shrink-0 ${value ? 'bg-violet-600' : 'bg-gray-300 dark:bg-gray-600'}`}>
      <span className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${value ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )
}

export default function TeacherSettings() {
  const { user, signOut }       = useAuth()
  const { dark, toggleTheme }   = useTheme()
  const navigate                = useNavigate()
  const [tab, setTab]           = useState('General')
  const [notifs, setNotifs]     = useState({ newStudent: true, weeklyReport: true, aiErrors: false })
  const [displayName, setDisplayName] = useState(user?.email?.split('@')[0] || '')

  const handleSignOut = async () => {
    await signOut()
    navigate('/login')
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
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">Display Name</label>
                  <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition" />
                </div>
                <div>
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">Email</label>
                  <input defaultValue={user?.email} disabled
                    className="w-full px-4 py-2.5 bg-gray-100 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl text-sm text-gray-400 cursor-not-allowed" />
                </div>
                <button onClick={() => toast.success('Saved!')}
                  className="px-5 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-xl text-sm font-bold transition shadow">
                  Save Changes
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

        {tab === 'Notifications' && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
            <h3 className="font-black text-gray-900 dark:text-white mb-5">Notification Preferences</h3>
            {[
              { key: 'newStudent',   label: 'New Student Enrolled', desc: 'When a student creates an account' },
              { key: 'weeklyReport', label: 'Weekly Report',        desc: 'Summary of class performance every Monday' },
              { key: 'aiErrors',     label: 'AI Generation Errors', desc: 'Alerts when question generation fails' },
            ].map(n => (
              <div key={n.key} className="flex items-center justify-between py-4 border-b border-gray-50 dark:border-gray-800 last:border-0">
                <div>
                  <p className="font-semibold text-gray-900 dark:text-white text-sm">{n.label}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{n.desc}</p>
                </div>
                <Toggle value={notifs[n.key]} onChange={v => setNotifs(p => ({ ...p, [n.key]: v }))} />
              </div>
            ))}
          </div>
        )}

        {tab === 'Security' && (
          <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm space-y-4">
            <h3 className="font-black text-gray-900 dark:text-white">Change Password</h3>
            {['Current Password', 'New Password', 'Confirm New Password'].map(l => (
              <div key={l}>
                <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-1.5">{l}</label>
                <input type="password" placeholder="••••••••"
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm dark:text-white outline-none focus:ring-2 focus:ring-violet-500 transition" />
              </div>
            ))}
            <button onClick={() => toast.success('Password updated!')}
              className="px-5 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-xl text-sm font-bold transition shadow">
              Update Password
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