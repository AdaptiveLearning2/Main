import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { BarChart3 } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { apiFetch } from '../../lib/api'

const TOPICS = ['ordering','rationals','expressions','algebra','geometry','angle_relationships','mean','median','mode','probability']
const COLORS  = ['#6366f1','#8b5cf6','#06b6d4','#10b981','#f59e0b','#ef4444','#ec4899','#84cc16','#f97316','#14b8a6']
const PIE_COLORS = { easy: '#10b981', medium: '#f59e0b', hard: '#ef4444' }

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl px-4 py-2 shadow-lg text-sm">
      <p className="font-bold text-gray-900 dark:text-white capitalize">{label}</p>
      <p className="text-violet-600">{payload[0].value} questions</p>
    </div>
  )
}

export default function TeacherAnalytics() {
  const [questions, setQuestions] = useState([])
  const [loading, setLoading]     = useState(true)

  useEffect(() => {
    apiFetch('/api/questions?limit=1000')
      .then(q => { setQuestions(q || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const topicData = TOPICS.map((t, i) => ({
    topic: t.replace('_', ' '),
    count: questions.filter(q => q.subject === t).length,
    fill:  COLORS[i],
  })).filter(d => d.count > 0)

  const diffData = ['easy','medium','hard'].map(d => ({
    name:  d,
    value: questions.filter(q => q.difficulty === d).length,
  })).filter(d => d.value > 0)

  const summaryCards = [
    { label: 'Total Questions', value: questions.length,                                     emoji: '❓', color: 'from-violet-500 to-purple-600' },
    { label: 'Easy',            value: questions.filter(q => q.difficulty === 'easy').length,   emoji: '🟢', color: 'from-green-500 to-emerald-600' },
    { label: 'Medium',          value: questions.filter(q => q.difficulty === 'medium').length, emoji: '🟡', color: 'from-amber-500 to-orange-500' },
    { label: 'Hard',            value: questions.filter(q => q.difficulty === 'hard').length,   emoji: '🔴', color: 'from-rose-500 to-red-600' },
  ]

  return (
    <div className="p-6 lg:p-8 pb-12 space-y-8">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-3xl font-black text-gray-900 dark:text-white flex items-center gap-3">
          <BarChart3 className="text-violet-600" size={28} /> Analytics
        </h1>
        <p className="text-gray-500 dark:text-gray-400 mt-1">Question bank distribution and insights.</p>
      </motion.div>

      {/* summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {summaryCards.map((c, i) => (
          <motion.div key={c.label}
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
            whileHover={{ y: -4 }}
            className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-5 shadow-sm"
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-bold uppercase tracking-widest text-gray-400 mb-1">{c.label}</p>
                <p className="text-3xl font-black text-gray-900 dark:text-white">{loading ? '—' : c.value}</p>
              </div>
              <div className={`w-10 h-10 bg-gradient-to-br ${c.color} rounded-xl flex items-center justify-center text-lg shadow`}>{c.emoji}</div>
            </div>
          </motion.div>
        ))}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* bar chart */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}
          className="lg:col-span-2 bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
          <h3 className="font-black text-gray-900 dark:text-white mb-6">Questions by Topic</h3>
          {loading ? (
            <div className="h-64 flex items-center justify-center">
              <div className="w-8 h-8 border-4 border-violet-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : topicData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-gray-400 text-sm">No questions yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={topicData} margin={{ top: 0, right: 0, left: -20, bottom: 50 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" className="dark:stroke-gray-700" />
                <XAxis dataKey="topic" tick={{ fontSize: 11, fill: '#94a3b8' }} angle={-35} textAnchor="end" interval={0} />
                <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} allowDecimals={false} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                  {topicData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </motion.div>

        {/* pie chart */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}
          className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-100 dark:border-gray-800 p-6 shadow-sm">
          <h3 className="font-black text-gray-900 dark:text-white mb-6">Difficulty Split</h3>
          {loading || diffData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-gray-400 text-sm">No data yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={diffData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={4} dataKey="value">
                  {diffData.map((d, i) => <Cell key={i} fill={PIE_COLORS[d.name]} />)}
                </Pie>
                <Tooltip formatter={(v, n) => [v, n.charAt(0).toUpperCase() + n.slice(1)]} />
                <Legend formatter={v => <span className="capitalize text-sm font-semibold text-gray-700 dark:text-gray-300">{v}</span>} />
              </PieChart>
            </ResponsiveContainer>
          )}
          <div className="space-y-2 mt-2">
            {diffData.map(d => (
              <div key={d.name} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full" style={{ background: PIE_COLORS[d.name] }} />
                  <span className="text-sm capitalize font-semibold text-gray-700 dark:text-gray-300">{d.name}</span>
                </div>
                <span className="text-sm font-black text-gray-900 dark:text-white">{d.value}</span>
              </div>
            ))}
          </div>
        </motion.div>
      </div>
    </div>
  )
}