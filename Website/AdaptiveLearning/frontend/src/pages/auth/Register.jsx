import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Mail, Lock, User, Eye, EyeOff, ArrowRight } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { toast } from 'sonner'

const ROLES = [
  { id: 'student', emoji: '🎓', title: 'Student',  sub: 'Practice & learn' },
  { id: 'teacher', emoji: '📚', title: 'Teacher',  sub: 'Teach & analyze' },
  { id: 'parent',  emoji: '👪', title: 'Parent',   sub: 'Monitor your child' },
]

const ROLE_HOME = { student: '/dashboard', teacher: '/teacher', parent: '/parent' }

function StrengthBar({ password }) {
  if (!password) return null
  const score = [password.length >= 8, /[A-Z]/.test(password), /[0-9]/.test(password), /[^A-Za-z0-9]/.test(password)].filter(Boolean).length
  const colors = ['', 'bg-rose-500', 'bg-amber-400', 'bg-yellow-400', 'bg-green-500']
  const labels = ['', 'Weak', 'Fair', 'Good', 'Strong']
  return (
    <div className="mt-2">
      <div className="flex gap-1 mb-1">{[1,2,3,4].map(i => <div key={i} className={`h-1.5 flex-1 rounded-full transition-colors ${i <= score ? colors[score] : 'bg-gray-200 dark:bg-gray-700'}`} />)}</div>
      <p className={`text-xs font-semibold ${score <= 1 ? 'text-rose-500' : score <= 2 ? 'text-amber-500' : 'text-green-500'}`}>{labels[score]}</p>
    </div>
  )
}

export default function Register() {
  const [email, setEmail]         = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword]   = useState('')
  const [confirm, setConfirm]     = useState('')
  const [role, setRole]           = useState('student')
  const [showPw, setShowPw]       = useState(false)
  const [loading, setLoading]     = useState(false)
  const { signUp } = useAuth()
  const navigate   = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (password !== confirm) return toast.error('Passwords do not match')
    if (password.length < 6)  return toast.error('Password must be at least 6 characters')
    setLoading(true)
    try {
      await signUp(email, password, role, displayName)
      navigate(ROLE_HOME[role] || '/dashboard')
      toast.success('Account created! Welcome 🎉')
    } catch (err) {
      toast.error(err.message || 'Failed to create account')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="w-full max-w-md mx-auto">
      <div className="text-center mb-8">
        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 200, delay: 0.1 }}
          className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-indigo-500 to-violet-600 rounded-2xl shadow-xl mb-5">
          <span className="text-3xl">🧠</span>
        </motion.div>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white">Create Account</h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1 text-sm">Join AdaptiveLearning — it's free</p>
      </div>

      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl border border-gray-100 dark:border-gray-800 p-8">
        {/* role picker */}
        <div className="mb-6">
          <p className="text-sm font-bold text-gray-700 dark:text-gray-300 mb-3">I am joining as...</p>
          <div className="grid grid-cols-3 gap-2">
            {ROLES.map(r => (
              <motion.button key={r.id} type="button" onClick={() => setRole(r.id)}
                whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
                className={`p-3 rounded-xl border-2 text-left transition-all ${role === r.id ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30' : 'border-gray-200 dark:border-gray-700 hover:border-indigo-300'}`}>
                <div className="text-xl mb-1">{r.emoji}</div>
                <div className="font-bold text-gray-900 dark:text-white text-xs">{r.title}</div>
                <div className="text-[10px] text-gray-500 dark:text-gray-400">{r.sub}</div>
              </motion.button>
            ))}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">Display Name</label>
            <div className="relative">
              <User size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                className="w-full pl-10 pr-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition text-sm"
                placeholder="Your name" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">Email</label>
            <div className="relative">
              <Mail size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                className="w-full pl-10 pr-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition text-sm"
                placeholder="you@example.com" required autoFocus />
            </div>
          </div>
          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">Password</label>
            <div className="relative">
              <Lock size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input type={showPw ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)}
                className="w-full pl-10 pr-11 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition text-sm"
                placeholder="Min 6 characters" required />
              <button type="button" onClick={() => setShowPw(p => !p)} className="absolute right-3.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-indigo-600 transition">
                {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <StrengthBar password={password} />
          </div>
          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1.5">Confirm Password</label>
            <div className="relative">
              <Lock size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input type={showPw ? 'text' : 'password'} value={confirm} onChange={e => setConfirm(e.target.value)}
                className={`w-full pl-10 pr-4 py-3 bg-gray-50 dark:bg-gray-800 border rounded-xl focus:ring-2 focus:ring-indigo-500 dark:text-white outline-none transition text-sm ${confirm && confirm !== password ? 'border-rose-400' : 'border-gray-200 dark:border-gray-700'}`}
                placeholder="••••••••" required />
            </div>
            {confirm && confirm !== password && <p className="text-xs text-rose-500 mt-1">Passwords don't match</p>}
          </div>

          {/* parent tip */}
          {role === 'parent' && (
            <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-3 text-xs text-amber-800 dark:text-amber-200">
              👪 After signing up, go to your dashboard and enter your child's <strong>User ID</strong> to link their account. They can find their ID in their Profile page.
            </div>
          )}

          <motion.button type="submit" disabled={loading}
            whileHover={{ scale: 1.01 }} whileTap={{ scale: 0.98 }}
            className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 text-white rounded-xl font-bold shadow-lg disabled:opacity-60 transition-all flex items-center justify-center gap-2 mt-2">
            {loading
              ? <motion.div animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} className="w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
              : <><span>Create {ROLES.find(r2 => r2.id === role)?.title} Account</span><ArrowRight size={16} /></>}
          </motion.button>
        </form>

        <p className="text-center text-gray-500 dark:text-gray-400 text-sm mt-6">
          Already have an account? <Link to="/login" className="text-indigo-600 font-bold hover:underline">Sign in →</Link>
        </p>
      </div>
    </div>
  )
}