import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { LayoutDashboard, Link as LinkIcon, Settings as SettingsIcon, LogOut, Moon, Sun, ChevronLeft, ChevronRight, Menu } from 'lucide-react'
import { useAuth }  from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import ErrorBoundary from '../components/ui/ErrorBoundary'
import MobileDrawer from '../components/ui/MobileDrawer'
import useCollapsedSidebar from '../hooks/useCollapsedSidebar'
import useMobileDrawer from '../hooks/useMobileDrawer'

const NAV = [
  { path: '/parent',      label: 'Dashboard',  icon: LayoutDashboard, exact: true },
  { path: '/parent/link', label: 'Link Child',  icon: LinkIcon },
  // The only place a parent can turn a withdrawn consent channel back on.
  { path: '/parent/settings', label: 'Settings', icon: SettingsIcon },
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
        <button aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'} onClick={toggleTheme} className={`flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition ${collapsed && !mobile ? 'justify-center' : ''}`}>
          {dark ? <Sun size={18} /> : <Moon size={18} />}
          {(!collapsed || mobile) && <span>{dark ? 'Light mode' : 'Dark mode'}</span>}
        </button>
        <button aria-label="Sign out" onClick={async () => { await signOut(); navigate('/login') }}
          className={`flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition ${collapsed && !mobile ? 'justify-center' : ''}`}>
          <LogOut size={18} />
          {(!collapsed || mobile) && <span>Sign out</span>}
        </button>
      </div>
    </div>
  )
}

export default function ParentLayout() {
  // Page-transition key. Must come from `useLocation()`, not
  // `window.location.pathname` directly, or React never sees it change and
  // the enter animation doesn't replay on navigation.
  const { pathname } = useLocation()
  // Scoped key so collapsing this sidebar doesn't collapse the other layouts'.
  const [collapsed, toggleCollapsed] = useCollapsedSidebar('parent')
  const { open: mobileOpen, onOpen: openMobile, onClose: closeMobile } =
    useMobileDrawer()
  const { dark, toggleTheme }       = useTheme()

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-50 dark:bg-gray-950">
      <motion.aside animate={{ width: collapsed ? 64 : 240 }} transition={{ duration: 0.2 }}
        className="hidden md:flex flex-col h-full bg-white dark:bg-gray-900 border-r border-gray-100 dark:border-gray-800 relative flex-shrink-0 overflow-hidden">
        <SidebarContent collapsed={collapsed} />
        <button aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          onClick={toggleCollapsed}
          className="absolute -right-3 top-20 w-6 h-6 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full shadow flex items-center justify-center text-gray-500 hover:text-emerald-600 transition z-10">
          {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
        </button>
      </motion.aside>

      <MobileDrawer open={mobileOpen} onClose={closeMobile} label="Navigation">
        <SidebarContent mobile onClose={closeMobile} />
      </MobileDrawer>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <div className="md:hidden flex items-center justify-between px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-100 dark:border-gray-800">
          <button aria-label="Open menu" onClick={openMobile} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"><Menu size={20} className="text-gray-600" /></button>
          <span className="text-sm font-black text-gray-900 dark:text-white">Parent <span className="text-emerald-600">Portal</span></span>
          <button aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'} onClick={toggleTheme} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">{dark ? <Sun size={18} className="text-gray-500" /> : <Moon size={18} className="text-gray-500" />}</button>
        </div>
        <main className="flex-1 overflow-y-auto">
          <motion.div key={pathname} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
            <ErrorBoundary resetKey={pathname}>
              <Outlet />
            </ErrorBoundary>
          </motion.div>
        </main>
      </div>
    </div>
  )
}