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
  // The fourth role, from the admin console (#125). It arrived while this
  // module was on another branch, so nothing conflicted and nothing flagged it
  // -- an admin hitting `/` got `homeFor` = null, fell through to `/dashboard`,
  // and was refused by the student guard onto the "no role assigned" screen.
  // Wrong rather than looping, which is the null-not-a-default rule below doing
  // its job; this is the entry that makes it right.
  //
  // `/admin` is guarded by AdminGuard on `profiles.role`, while the `role` this
  // map is keyed by comes from `user_metadata.role`. The two agree for an admin
  // whose metadata says so, and where they disagree AdminGuard is the one that
  // decides access -- this only decides where to *send* someone.
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
