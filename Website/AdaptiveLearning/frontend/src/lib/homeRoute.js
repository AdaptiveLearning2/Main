// Where each role's app starts; the only copy of this map.
export const HOME_BY_ROLE = {
  student: '/dashboard',
  teacher: '/teacher',
  parent:  '/parent',
  // Navigation only; `AdminGuard` is the access check.
  admin:   '/admin',
}

/** The route this role lands on, or null (never a guess, which could loop). */
export function homeFor(role) {
  return HOME_BY_ROLE[role] || null
}
