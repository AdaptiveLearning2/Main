import { matchPath } from 'react-router-dom'

// First match wins: a specific route (`/teacher/sessions/:id`) must precede its prefix.
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
