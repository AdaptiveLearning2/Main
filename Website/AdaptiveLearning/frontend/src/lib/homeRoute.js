// Where each role's app starts. `RoleGuard` uses this map (not a ternary,
// which breaks down past two roles) to send a user back to their own home.
// Kept in one place because `/` needs the same answer, and a second copy
// would drift.
export const HOME_BY_ROLE = {
  student: '/dashboard',
  teacher: '/teacher',
  parent:  '/parent',
  // `AdminGuard` is the real access check (on `profiles.role`); this map
  // only decides where to send someone, using the less trustworthy
  // `user_metadata.role`.
  admin:   '/admin',
}

/** The route this role should land on, or null if unrecognized. Returns
 * null rather than guessing a default, since a wrong guess could send a
 * user to a route they can't see and loop forever. `RoleGuard` handles null
 * by showing an explanation instead of navigating.
 */
export function homeFor(role) {
  return HOME_BY_ROLE[role] || null
}
