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
const CAPTCHA  = 'The verification check did not pass. Complete it and try again.'

// No status is a request that never got an answer; 5xx is a service that failed
// to give one. Neither is a statement about the email or the password, and
// saying "incorrect" for them sends someone to reset a password that is fine.
const unanswered = err => !err?.status || err.status >= 500

const throttled = err => err.status === 429 || String(err.code || '').startsWith('over_')

// Supabase answers an unknown email and a wrong password alike, with
// `invalid_credentials`. A refusal with no code is an older server saying the
// same thing.
const badCredentials = err =>
  err.code === 'invalid_credentials' || err.code === 'user_not_found'
  || (!err.code && err.status === 400)

export function signInMessage(err) {
  if (unanswered(err)) return "Couldn't reach the sign-in service. Check your connection and try again."
  if (throttled(err)) return TOO_MANY
  if (badCredentials(err)) return 'Email or password is incorrect.'
  // Only reachable with the right password, so it tells nobody anything they
  // could not find out by signing in.
  if (err.code === 'email_not_confirmed') return 'Confirm your email address first. The link is in your inbox.'
  if (err.code === 'captcha_failed') return CAPTCHA
  // Anything else -- a banned account, a hook refusing -- is not a wrong
  // password, and saying so sends someone to reset one that is fine.
  return "Couldn't sign you in. If this keeps happening, contact your school."
}

// GoTrue's `weak_password.reasons`, which auth-js carries on the error.
const WEAK_REASONS = {
  length:     'it is too short',
  characters: 'it needs more kinds of character (letters, digits, symbols)',
  pwned:      'it has appeared in a known data breach',
}

function weakPasswordMessage(err) {
  const why = (err.reasons || []).map(r => WEAK_REASONS[r]).filter(Boolean)
  return why.length
    ? `Choose a stronger password: ${why.join('; ')}.`
    : 'Choose a stronger password.'
}

export function signUpMessage(err) {
  if (unanswered(err)) return "Couldn't reach the sign-up service. Check your connection and try again."
  if (throttled(err)) return TOO_MANY
  // About the input, not about whether an account exists.
  if (err.code === 'weak_password') return weakPasswordMessage(err)
  if (err.code === 'email_address_invalid') return 'Enter a valid email address.'
  if (err.code === 'captcha_failed') return CAPTCHA
  // Refusals that are about the service, not the account, so saying what
  // happened gives nothing away.
  if (err.code === 'signup_disabled' || String(err.code || '').startsWith('hook_')) {
    return "Couldn't create an account right now. If this keeps happening, contact your school."
  }
  // "Already registered", and any code not recognised above, worded so it does
  // not say which it was. The default rather than a list, so a new code for a
  // taken address cannot arrive with a sentence of its own.
  return "We couldn't create an account with those details. If you already have one, sign in instead."
}
