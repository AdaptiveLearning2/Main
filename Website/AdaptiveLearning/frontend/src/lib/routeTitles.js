import { matchPath } from 'react-router-dom'

// Order matters: the first match wins and patterns overlap, so a more
// specific route (`/teacher/sessions/:id`) must come before its prefix
// (`/teacher/sessions`).
//
// Kept as one list here instead of a title hook in each of the 25 page
// components, so it stays easy to check against the route table at a glance.
//
// Lives in `lib/`, not beside a component, because a file that exports both
// a component and a helper breaks fast refresh for the whole file.
const TITLES = [
  ['/login',                        'Sign in'],
  ['/register',                     'Create account'],

  ['/dashboard',                    'Dashboard'],
  ['/practice',                     'Practice'],
  ['/adaptive',                     'AI session'],
  ['/history',                      'History'],
  ['/profile',                      'Profile'],
  ['/leaderboard',                  'Leaderboard'],
  ['/achievements',                 'Achievements'],
  ['/join-class',                   'Join a class'],

  ['/teacher/sessions/:sessionId',  'Session review'],
  ['/teacher/students/:id/report',  'Student report'],
  ['/teacher/classes/:id',          'Class'],
  ['/teacher/live',                 'Live monitoring'],
  ['/teacher/classes',              'Classes'],
  ['/teacher/students',             'Students'],
  ['/teacher/questions',            'Questions'],
  ['/teacher/analytics',            'Analytics'],
  ['/teacher/settings',             'Settings'],
  ['/teacher/sessions',             'Sessions'],
  ['/teacher',                      'Teacher dashboard'],

  ['/parent/child/:id',             'Child'],
  ['/parent/link',                  'Link a child'],
  ['/parent/settings',              'Settings'],
  ['/parent',                       'Parent dashboard'],
]

export { TITLES }

export function titleForPath(pathname) {
  for (const [pattern, title] of TITLES) {
    if (matchPath({ path: pattern, end: true }, pathname)) return title
  }
  return null
}
