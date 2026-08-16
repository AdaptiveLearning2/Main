// Where each role's app actually starts.
//
// `RoleGuard` sends a user who reached a route they may not see back to their
// own home. It used to compute that as `role === 'teacher' ? '/teacher' :
// '/dashboard'`, which is a **loop** for a parent: `/dashboard` is student-only,
// so the guard rejects it and sends them to `/dashboard` again. Anyone who
// followed a stale teacher link, or typed `/`, bounced until the browser gave
// up. The two-branch shape hid it -- there are three roles.
//
// Kept here rather than inline in the guard because `/` needs the same answer,
// and a second copy of this map is how the two come to disagree.
export const HOME_BY_ROLE = {
  student: '/dashboard',
  teacher: '/teacher',
  parent:  '/parent',
}

/** The route this role should land on, or null if we do not recognise it.
 *
 * **Null rather than a default**, and that is the whole safety property. Any
 * fallback here is a route some role is not allowed to see, so guessing
 * recreates exactly the loop this module exists to prevent -- a user whose
 * profile carries no role, or a role added to the database before the routes
 * exist, would bounce for ever. The caller has to handle "we don't know", and
 * `RoleGuard` handles it by rendering an explanation instead of navigating.
 */
export function homeFor(role) {
  return HOME_BY_ROLE[role] || null
}
