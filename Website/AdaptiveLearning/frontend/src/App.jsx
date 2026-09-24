import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'
import { ThemeProvider }  from './context/ThemeContext'
import ThemedToaster    from './components/ui/ThemedToaster'
import { AuthProvider }   from './context/AuthContext'
import AuthLayout         from './layout/AuthLayout'
import StudentLayout      from './layout/StudentLayout'
import TeacherLayout      from './layout/TeacherLayout'
import ParentLayout       from './layout/ParentLayout'
import AdminLayout        from './layout/AdminLayout'
import RoleGuard          from './components/auth/RoleGuard'
import HomeRedirect       from './components/auth/HomeRedirect'
import AdminGuard         from './components/auth/AdminGuard'
import ScrollToTop        from './components/ui/ScrollToTop'
import RouteTitle         from './components/ui/RouteTitle'
import PageLoader         from './components/ui/PageLoader'

// Pages are lazy per route; layouts, guards and auth pages stay static (critical path).
import Login    from './pages/auth/Login'
import Register from './pages/auth/Register'

const StudentDashboard = lazy(() => import('./pages/student/Dashboard'))
const Practice         = lazy(() => import('./pages/student/Practice'))
const Adaptive         = lazy(() => import('./pages/student/Adaptive'))
const History          = lazy(() => import('./pages/student/History'))
const Profile          = lazy(() => import('./pages/student/Profile'))
const Leaderboard      = lazy(() => import('./pages/student/Leaderboard'))
const Achievements     = lazy(() => import('./pages/student/Achievements'))
const JoinClass        = lazy(() => import('./pages/student/JoinClass'))

const TeacherDashboard = lazy(() => import('./pages/teacher/Dashboard'))
const Students         = lazy(() => import('./pages/teacher/Students'))
const Questions        = lazy(() => import('./pages/teacher/Questions'))
const Analytics        = lazy(() => import('./pages/teacher/Analytics'))
const TeacherSettings  = lazy(() => import('./pages/teacher/Settings'))
const Classes          = lazy(() => import('./pages/teacher/Classes'))
const ClassDetail      = lazy(() => import('./pages/teacher/ClassDetail'))
const Live             = lazy(() => import('./pages/teacher/Live'))
const SessionReview    = lazy(() => import('./pages/teacher/SessionReview'))
const Sessions         = lazy(() => import('./pages/teacher/Sessions'))
const StudentReport    = lazy(() => import('./pages/teacher/StudentReport'))

const ParentDashboard  = lazy(() => import('./pages/parent/Dashboard'))
const ParentLinkChild  = lazy(() => import('./pages/parent/LinkChild'))
const ParentChild      = lazy(() => import('./pages/parent/ChildDetail'))
const ParentSettings   = lazy(() => import('./pages/parent/Settings'))

const AdminOverview    = lazy(() => import('./pages/admin/Overview'))
const AdminFlags       = lazy(() => import('./pages/admin/Flags'))
const AdminLiveFlow    = lazy(() => import('./pages/admin/LiveFlow'))
const AdminSchoolYear  = lazy(() => import('./pages/admin/SchoolYear'))
const AdminSecurity    = lazy(() => import('./pages/admin/SecurityEvents'))

const NotFound = lazy(() => import('./pages/NotFound'))

export default function App() {
  return (
    // Honours the OS "reduce motion" setting for every animation, at the provider
    // so new ones are covered too. Fades still run; spinners stop.
    <MotionConfig reducedMotion="user">
    <ThemeProvider>
      <ThemedToaster />
      <AuthProvider>
        <BrowserRouter>
          <ScrollToTop />
          <RouteTitle />
          {/* One boundary for all routes, so loaded sections don't flash the loader. */}
          <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route element={<AuthLayout />}>
              <Route path="/login"    element={<Login />} />
              <Route path="/register" element={<Register />} />
            </Route>

            {/* student */}
            <Route element={<RoleGuard roles={['student']}><StudentLayout /></RoleGuard>}>
              <Route path="/dashboard"    element={<StudentDashboard />} />
              <Route path="/practice"     element={<Practice />} />
              <Route path="/adaptive"     element={<Adaptive />} />
              <Route path="/history"      element={<History />} />
              <Route path="/profile"      element={<Profile />} />
              <Route path="/leaderboard"  element={<Leaderboard />} />
              <Route path="/achievements" element={<Achievements />} />
              <Route path="/join-class"   element={<JoinClass />} />
            </Route>

            {/* teacher */}
            <Route element={<RoleGuard roles={['teacher']}><TeacherLayout /></RoleGuard>}>
              <Route path="/teacher"                     element={<TeacherDashboard />} />
              <Route path="/teacher/live"                element={<Live />} />
              <Route path="/teacher/sessions/:sessionId" element={<SessionReview />} />
              <Route path="/teacher/classes"             element={<Classes />} />
              <Route path="/teacher/classes/:id"         element={<ClassDetail />} />
              <Route path="/teacher/students"            element={<Students />} />
              <Route path="/teacher/students/:id/report" element={<StudentReport />} />
              <Route path="/teacher/questions"           element={<Questions />} />
              <Route path="/teacher/analytics"           element={<Analytics />} />
              <Route path="/teacher/settings"            element={<TeacherSettings />} />
              <Route path="/teacher/sessions"            element={<Sessions />} />
            </Route>

            {/* parent */}
            <Route element={<RoleGuard roles={['parent']}><ParentLayout /></RoleGuard>}>
              <Route path="/parent"              element={<ParentDashboard />} />
              <Route path="/parent/link"         element={<ParentLinkChild />} />
              <Route path="/parent/child/:id"    element={<ParentChild />} />
              <Route path="/parent/settings"     element={<ParentSettings />} />
            </Route>

            {/* AdminGuard asks the backend; it never trusts a client-side role. */}
            <Route element={<AdminGuard><AdminLayout /></AdminGuard>}>
              <Route path="/admin"       element={<AdminOverview />} />
              <Route path="/admin/flags" element={<AdminFlags />} />
              <Route path="/admin/live"  element={<AdminLiveFlow />} />
              <Route path="/admin/year"  element={<AdminSchoolYear />} />
              <Route path="/admin/security" element={<AdminSecurity />} />
            </Route>

            {/* Role-aware home, via `homeFor`. */}
            <Route path="/"  element={<HomeRedirect />} />
            <Route path="*"  element={<NotFound />} />
          </Routes>
          </Suspense>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
    </MotionConfig>
  )
}