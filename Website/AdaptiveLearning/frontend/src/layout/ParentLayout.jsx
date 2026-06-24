import { useState } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { LayoutDashboard, Link as LinkIcon, LogOut, Moon, Sun, ChevronLeft, ChevronRight, Menu, X } from 'lucide-react'
import { useAuth }  from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'

const NAV = [
  { path: '/parent',      label: 'Dashboard',  icon: LayoutDashboard, exact: true },
  { path: '/parent/link', label: 'Link Child',  icon: LinkIcon },
]

function SidebarContent({ collapsed, mobile, onClose }) {
  const { user, signOut } = useAuth()
  const { dark, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const initials = user?.email?.[0]?.toUpperCase() || '?'

  return (
    <div className="flex flex-col h-full">
      <div className={`flex items-center gap-3 px-4 py-5 border-b border-gray-100 dark:border-gray-800 ${collapsed && !mobile ? 'justify-center' : ''}`}>
        <div className="w-9 h-9 flex-shrink-0 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-xl flex items-center justify-center text-white font-bold shadow-md">👪</div>
        {(!collapsed || mobile) && (
          <div>
            <p className="font-black text-gray-900 dark:text-white text-sm leading-tight">Parent</p>
            <p className="font-black text-emerald-600 text-sm leading-tight">Portal</p>
          </div>
        )}
      </div>

      {(!collapsed || mobile) && (
        <div className="px-4 pt-4">
          <div className="bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-100 dark:border-emerald-800 rounded-xl px-3 py-2 flex items-center gap-2">
            <div className="w-7 h-7 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0">{initials}</div>
            <div className="min-w-0">
              <p className="text-xs font-bold text-gray-900 dark:text-white truncate">{user?.email?.split('@')[0]}</p>
              <p className="text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold">👪 Parent</p>
            </div>
          </div>
        </div>
      )}

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.map(({ path, label, icon: Icon, exact }) => (
          <NavLink key={path} to={path} end={!!exact} onClick={() => mobile && onClose?.()}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-150 group relative
              ${isActive ? 'bg-emerald-600 text-white shadow-md' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}
              ${collapsed && !mobile ? 'justify-center px-2' : ''}`
            }>
            <Icon size={18} className="flex-shrink-0" />
            {(!collapsed || mobile) && <span>{label}</span>}
            {collapsed && !mobile && (
              <span className="absolute left-full ml-2 px-2 py-1 bg-gray-900 text-white text-xs rounded-lg opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-50 transition-opacity">{label}</span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="px-3 pb-4 pt-3 border-t border-gray-100 dark:border-gray-800 space-y-1">
        <button onClick={toggleTheme} className={`flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition ${collapsed && !mobile ? 'justify-center' : ''}`}>
          {dark ? <Sun size={18} /> : <Moon size={18} />}
          {(!collapsed || mobile) && <span>{dark ? 'Light mode' : 'Dark mode'}</span>}
        </button>
        <button onClick={async () => { await signOut(); navigate('/login') }}
          className={`flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition ${collapsed && !mobile ? 'justify-center' : ''}`}>
          <LogOut size={18} />
          {(!collapsed || mobile) && <span>Sign out</span>}
        </button>
      </div>
    </div>
  )
}

export default function ParentLayout() {
  const [collapsed, setCollapsed]   = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const { dark, toggleTheme }       = useTheme()

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-50 dark:bg-gray-950">
      <motion.aside animate={{ width: collapsed ? 64 : 240 }} transition={{ duration: 0.2 }}
        className="hidden md:flex flex-col h-full bg-white dark:bg-gray-900 border-r border-gray-100 dark:border-gray-800 relative flex-shrink-0 overflow-hidden">
        <SidebarContent collapsed={collapsed} />
        <button onClick={() => setCollapsed(c => !c)}
          className="absolute -right-3 top-20 w-6 h-6 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full shadow flex items-center justify-center text-gray-500 hover:text-emerald-600 transition z-10">
          {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
        </button>
      </motion.aside>

      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/50 z-40 md:hidden" onClick={() => setMobileOpen(false)} />
            <motion.aside initial={{ x: '-100%' }} animate={{ x: 0 }} exit={{ x: '-100%' }} transition={{ type: 'spring', damping: 25, stiffness: 200 }}
              className="fixed left-0 top-0 bottom-0 w-72 bg-white dark:bg-gray-900 z-50 md:hidden shadow-2xl overflow-y-auto">
              <button onClick={() => setMobileOpen(false)} className="absolute top-4 right-4 p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"><X size={18} className="text-gray-500" /></button>
              <SidebarContent mobile onClose={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <div className="md:hidden flex items-center justify-between px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-100 dark:border-gray-800">
          <button onClick={() => setMobileOpen(true)} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"><Menu size={20} className="text-gray-600" /></button>
          <span className="text-sm font-black text-gray-900 dark:text-white">Parent <span className="text-emerald-600">Portal</span></span>
          <button onClick={toggleTheme} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">{dark ? <Sun size={18} className="text-gray-500" /> : <Moon size={18} className="text-gray-500" />}</button>
        </div>
        <main className="flex-1 overflow-y-auto">
          <motion.div key={window.location.pathname} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
            <Outlet />
          </motion.div>
        </main>
      </div>
    </div>
  )
}