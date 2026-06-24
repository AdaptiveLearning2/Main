import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ThemeProvider }  from './context/ThemeContext'
import { AuthProvider }   from './context/AuthContext'
import AuthLayout         from './layout/AuthLayout'
import StudentLayout      from './layout/StudentLayout'
import TeacherLayout      from './layout/TeacherLayout'
import ParentLayout       from './layout/ParentLayout'
import RoleGuard          from './components/auth/RoleGuard'
import ScrollToTop        from './components/ui/ScrollToTop'

import Login    from './pages/auth/Login'
import Register from './pages/auth/Register'

import StudentDashboard from './pages/student/Dashboard'
import Practice         from './pages/student/Practice'
import Adaptive         from './pages/student/Adaptive'
import History          from './pages/student/History'
import Profile          from './pages/student/Profile'
import Leaderboard      from './pages/student/Leaderboard'
import Achievements     from './pages/student/Achievements'
import JoinClass        from './pages/student/JoinClass'

import TeacherDashboard from './pages/teacher/Dashboard'
import Students         from './pages/teacher/Students'
import Questions        from './pages/teacher/Questions'
import Analytics        from './pages/teacher/Analytics'
import TeacherSettings  from './pages/teacher/Settings'
import Classes          from './pages/teacher/Classes'
import Live             from './pages/teacher/Live'
import SessionReview    from './pages/teacher/SessionReview'
import Sessions         from './pages/teacher/Sessions'

import ParentDashboard  from './pages/parent/Dashboard'
import ParentLinkChild  from './pages/parent/LinkChild'
import ParentChild      from './pages/parent/ChildDetail'

import NotFound from './pages/NotFound'

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Toaster position="top-right" richColors closeButton />
        <BrowserRouter>
          <ScrollToTop />
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
              <Route path="/teacher/students"            element={<Students />} />
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
            </Route>

            <Route path="/"  element={<Navigate to="/dashboard" replace />} />
            <Route path="*"  element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  )
}