import { matchPath } from 'react-router-dom'

// One import() per page, shared by App's React.lazy and the boot prefetch below, so a
// prefetched chunk is the very module the route renders.
export const pages = {
  studentDashboard: () => import('./pages/student/Dashboard'),
  practice:         () => import('./pages/student/Practice'),
  adaptive:         () => import('./pages/student/Adaptive'),
  history:          () => import('./pages/student/History'),
  profile:          () => import('./pages/student/Profile'),
  leaderboard:      () => import('./pages/student/Leaderboard'),
  achievements:     () => import('./pages/student/Achievements'),
  joinClass:        () => import('./pages/student/JoinClass'),

  teacherDashboard: () => import('./pages/teacher/Dashboard'),
  students:         () => import('./pages/teacher/Students'),
  questions:        () => import('./pages/teacher/Questions'),
  analytics:        () => import('./pages/teacher/Analytics'),
  teacherSettings:  () => import('./pages/teacher/Settings'),
  classes:          () => import('./pages/teacher/Classes'),
  classDetail:      () => import('./pages/teacher/ClassDetail'),
  live:             () => import('./pages/teacher/Live'),
  sessionReview:    () => import('./pages/teacher/SessionReview'),
  sessions:         () => import('./pages/teacher/Sessions'),
  studentReport:    () => import('./pages/teacher/StudentReport'),

  parentDashboard:  () => import('./pages/parent/Dashboard'),
  parentLinkChild:  () => import('./pages/parent/LinkChild'),
  parentChild:      () => import('./pages/parent/ChildDetail'),
  parentSettings:   () => import('./pages/parent/Settings'),

  adminOverview:    () => import('./pages/admin/Overview'),
  adminFlags:       () => import('./pages/admin/Flags'),
  adminLiveFlow:    () => import('./pages/admin/LiveFlow'),
  adminSchoolYear:  () => import('./pages/admin/SchoolYear'),
  adminSecurity:    () => import('./pages/admin/SecurityEvents'),
  adminEngine:      () => import('./pages/admin/Engine'),
  adminStations:    () => import('./pages/admin/Stations'),
  adminStudentReport: () => import('./pages/admin/StudentReport'),

  notFound:         () => import('./pages/NotFound'),
}

// The paths App routes to each page. A path missing here is only not prefetched.
export const ROUTES = [
  ['/dashboard', 'studentDashboard'], ['/practice', 'practice'], ['/adaptive', 'adaptive'],
  ['/history', 'history'], ['/profile', 'profile'], ['/leaderboard', 'leaderboard'],
  ['/achievements', 'achievements'], ['/join-class', 'joinClass'],
  ['/teacher', 'teacherDashboard'], ['/teacher/live', 'live'],
  ['/teacher/sessions/:sessionId', 'sessionReview'], ['/teacher/classes', 'classes'],
  ['/teacher/classes/:id', 'classDetail'], ['/teacher/students', 'students'],
  ['/teacher/students/:id/report', 'studentReport'], ['/teacher/questions', 'questions'],
  ['/teacher/analytics', 'analytics'], ['/teacher/settings', 'teacherSettings'],
  ['/teacher/sessions', 'sessions'],
  ['/parent', 'parentDashboard'], ['/parent/link', 'parentLinkChild'],
  ['/parent/child/:id', 'parentChild'], ['/parent/settings', 'parentSettings'],
  ['/admin', 'adminOverview'], ['/admin/flags', 'adminFlags'], ['/admin/live', 'adminLiveFlow'],
  ['/admin/year', 'adminSchoolYear'], ['/admin/security', 'adminSecurity'],
  ['/admin/engine', 'adminEngine'], ['/admin/stations', 'adminStations'],
  ['/admin/students/:id/report', 'adminStudentReport'],
]

/**
 * Start loading the page chunk for `pathname` now, beside the role read that gates its render.
 * The guard may still redirect; the chunk is public, so the cost is a wasted download.
 */
export function prefetchRoute(pathname) {
  const hit = ROUTES.find(([path]) => matchPath(path, pathname))
  if (!hit) return null
  return pages[hit[1]]().catch(() => null)
}
