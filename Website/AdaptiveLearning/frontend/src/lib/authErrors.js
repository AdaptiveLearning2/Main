// What the sign-in and sign-up pages say when Supabase refuses.
//
// Supabase's own `message` is never shown. It is worded for a developer, and on
// sign-up it reads "User already registered" -- which tells anyone typing an
// address whether that person has an account here. So each page gets a fixed
// sentence per *kind* of refusal, chosen from `status` and `code`, and every
// refusal that is about the account rather than the input shares one sentence.
//
// The limit, stated so nobody mistakes this for more than it is: with email
// confirmation off, sign-up for a new address signs the person in and for a
// taken one does not, and that difference shows whatever the wording. Only
// turning confirmation on closes it -- Supabase then answers both the same way.

const TOO_MANY = 'Too many attempts. Wait a few minutes and try again.'

// No status is a request that never got an answer; 5xx is a service that failed
// to give one. Neither is a statement about the email or the password, and
// saying "incorrect" for them sends someone to reset a password that is fine.
const unanswered = err => !err?.status || err.status >= 500

const throttled = err => err.status === 429 || String(err.code || '').startsWith('over_')

export function signInMessage(err) {
  if (unanswered(err)) return "Couldn't reach the sign-in service. Check your connection and try again."
  if (throttled(err)) return TOO_MANY
  // Only reachable with the right password, so it tells nobody anything they
  // could not find out by signing in.
  if (err.code === 'email_not_confirmed') return 'Confirm your email address first. The link is in your inbox.'
  // Unknown email and wrong password alike.
  return 'Email or password is incorrect.'
}

export function signUpMessage(err) {
  if (unanswered(err)) return "Couldn't reach the sign-up service. Check your connection and try again."
  if (throttled(err)) return TOO_MANY
  // About the input, not about whether an account exists.
  if (err.code === 'weak_password') return 'Choose a stronger password.'
  if (err.code === 'email_address_invalid') return 'Enter a valid email address.'
  // "Already registered" among them, worded so it does not say which it was.
  return "We couldn't create an account with those details. If you already have one, sign in instead."
}
