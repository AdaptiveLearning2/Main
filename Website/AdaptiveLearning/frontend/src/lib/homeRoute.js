// Where each role's app actually starts.
//
// `RoleGuard` sends a user who reached a route they may not see back to their
// own home, computed from a role-to-route map rather than a two-branch
// ternary -- a two-branch check for three-plus roles produces a loop for
// whichever role isn't named, bouncing between "refused" and "sent right
// back". Kept here rather than inline in the guard because `/` needs the
// same answer, and a second copy of this map is how the two come to
// disagree.
export const HOME_BY_ROLE = {
  student: '/dashboard',
  teacher: '/teacher',
  parent:  '/parent',
  // `/admin` is guarded by AdminGuard on `profiles.role`, while the `role`
  // this map is keyed by comes from `user_metadata.role`. The two agree for
  // an admin whose metadata says so, and where they disagree AdminGuard is
  // the one that decides access -- this only decides where to *send* someone.
  admin:   '/admin',
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
