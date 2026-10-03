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
import { pages }          from './routes'
import ServerWaking       from './components/ui/ServerWaking'

// Pages are lazy per route; layouts, guards and auth pages stay static (critical path).
import Login    from './pages/auth/Login'
import Register from './pages/auth/Register'

const StudentDashboard = lazy(pages.studentDashboard)
const Practice         = lazy(pages.practice)
const Adaptive         = lazy(pages.adaptive)
const History          = lazy(pages.history)
const Profile          = lazy(pages.profile)
const Leaderboard      = lazy(pages.leaderboard)
const Achievements     = lazy(pages.achievements)
const JoinClass        = lazy(pages.joinClass)

const TeacherDashboard = lazy(pages.teacherDashboard)
const Students         = lazy(pages.students)
const Questions        = lazy(pages.questions)
const Analytics        = lazy(pages.analytics)
const TeacherSettings  = lazy(pages.teacherSettings)
const Classes          = lazy(pages.classes)
const ClassDetail      = lazy(pages.classDetail)
const Live             = lazy(pages.live)
const SessionReview    = lazy(pages.sessionReview)
const Sessions         = lazy(pages.sessions)
const StudentReport    = lazy(pages.studentReport)

const ParentDashboard  = lazy(pages.parentDashboard)
const ParentLinkChild  = lazy(pages.parentLinkChild)
const ParentChild      = lazy(pages.parentChild)
const ParentSettings   = lazy(pages.parentSettings)

const AdminOverview    = lazy(pages.adminOverview)
const AdminFlags       = lazy(pages.adminFlags)
const AdminLiveFlow    = lazy(pages.adminLiveFlow)
const AdminSchoolYear  = lazy(pages.adminSchoolYear)
const AdminSecurity    = lazy(pages.adminSecurity)
const AdminStudentReport = lazy(pages.adminStudentReport)

const NotFound = lazy(pages.notFound)

export default function App() {
  return (
    // Honours the OS "reduce motion" setting for every animation, at the provider
    // so new ones are covered too. Fades still run; spinners stop.
    <MotionConfig reducedMotion="user">
    <ThemeProvider>
      <ThemedToaster />
      {/* Above the auth provider: its role read is the first request a cold start waits on. */}
      <ServerWaking />
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
              <Route path="/admin/students/:id/report" element={<AdminStudentReport />} />
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