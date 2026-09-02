import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'
import { ThemeProvider }  from './context/ThemeContext'
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

// Pages are split per route. Statically imported, every page landed in one
// bundle: a student on a school laptop downloaded the teacher's analytics and
// the parent's consent screens -- which they can never open -- before their
// own dashboard could paint. The heaviest pages are the ones fewest people
// see (Recharts on the teacher and parent reporting surfaces, the adaptive
// session's sidecar client), so the split is worth most exactly where the
// static bundle cost most.
//
// The layouts, the guards and the auth pages stay static. They are on the
// critical path for every visit, so splitting them would add a network
// round trip to the first paint to save nothing.
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

// Split like every other section. These arrived static, from a branch cut
// before the split existed -- and they are the pages with the fewest viewers in
// the whole app, so leaving them eager would put the entire admin console in
// the bundle every student downloads.
const AdminOverview    = lazy(() => import('./pages/admin/Overview'))
const AdminFlags       = lazy(() => import('./pages/admin/Flags'))
const AdminLiveFlow    = lazy(() => import('./pages/admin/LiveFlow'))
const AdminSchoolYear  = lazy(() => import('./pages/admin/SchoolYear'))

const NotFound = lazy(() => import('./pages/NotFound'))

export default function App() {
  return (
    // `reducedMotion="user"` honours the OS "reduce motion" setting, which
    // nothing here did. Every page animates, and several animate *for ever* --
    // a rotating icon on both dashboards, a bouncing emoji on the 404 and on
    // the adaptive session's idle screen -- so a user who has asked their
    // system for less motion got a permanent, un-stoppable loop instead.
    //
    // At the provider rather than per call site, deliberately. The per-site fix
    // is `useReducedMotion()` and clearing *both* `animate` and `transition`,
    // which is a rule every future animation has to remember; there are already
    // eleven infinite loops across ten files. This is one line that covers them
    // and everything added later.
    //
    // "user" disables transform and layout animation while leaving opacity and
    // colour alone, which is the right cut: the vestibular trigger is movement,
    // and a fade carries none of it. Loading spinners stop spinning under it --
    // accepted, and the better trade. `PageLoader` says "Loading..." in text
    // beside its ring, and a still ring is a smaller harm than motion someone
    // has explicitly asked not to be shown.
    <MotionConfig reducedMotion="user">
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <ScrollToTop />
          <RouteTitle />
          {/* One boundary around the whole table rather than one per route.
              The fallback only shows while a chunk is in flight, which is once
              per page per visit; nesting them per route would flash the loader
              on every navigation within an already-loaded section. */}
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

            {/* AdminGuard, not RoleGuard: admin is `profiles.role`, which the
                backend reads and the client cannot write, never the
                `user_metadata.role` claim RoleGuard goes on. */}
            <Route element={<AdminGuard><AdminLayout /></AdminGuard>}>
              <Route path="/admin"       element={<AdminOverview />} />
              <Route path="/admin/flags" element={<AdminFlags />} />
              <Route path="/admin/live"  element={<AdminLiveFlow />} />
              <Route path="/admin/year"  element={<AdminSchoolYear />} />
            </Route>

            {/* Role-aware, not hardcoded to the student home. Sending every
                role to /dashboard meant a parent landing on / took a bounce
                through a route they may not see -- which was an infinite one
                until RoleGuard learned the third role. There are four now, and
                `homeFor` knows about the fourth. */}
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