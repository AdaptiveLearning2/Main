import { matchPath } from 'react-router-dom'

// Ordered, because the first match wins and the patterns overlap:
// `/teacher/sessions/:id` has to be tested before `/teacher/sessions`, or a
// session review is titled "Sessions".
//
// Kept as one list beside the route table it mirrors, rather than pushed into
// 25 page components. One list is checkable against App.jsx at a glance; 25
// hook calls are not, and the pages that would quietly go without one are the
// rarely opened ones nobody notices.
//
// In `lib/` rather than beside the component so the file exports only data and
// a pure function -- a module that exports both a component and a helper
// breaks fast refresh for the whole file.
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
