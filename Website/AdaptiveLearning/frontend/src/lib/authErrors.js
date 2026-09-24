// What sign-in and sign-up say when Supabase refuses. Supabase's own `message`
// is never shown ("User already registered" reveals whether an account exists);
// account-level refusals share one sentence. With email confirmation off,
// sign-up still leaks existence by whether it signs in.

const TOO_MANY = 'Too many attempts. Wait a few minutes and try again.'
const CAPTCHA  = 'The verification check did not pass. Complete it and try again.'

// No status or 5xx: never answered, so not a claim about the credentials.
const unanswered = err => !err?.status || err.status >= 500

const throttled = err => err.status === 429 || String(err.code || '').startsWith('over_')

// Unknown email and wrong password look alike; no code is an older server.
const badCredentials = err =>
  err.code === 'invalid_credentials' || err.code === 'user_not_found'
  || (!err.code && err.status === 400)

export function signInMessage(err) {
  if (unanswered(err)) return "Couldn't reach the sign-in service. Check your connection and try again."
  if (throttled(err)) return TOO_MANY
  if (badCredentials(err)) return 'Email or password is incorrect.'
  // Only reachable with the right password, so it leaks nothing.
  if (err.code === 'email_not_confirmed') return 'Confirm your email address first. The link is in your inbox.'
  if (err.code === 'captcha_failed') return CAPTCHA
  // Anything else (banned, hook refusal) is not a wrong password.
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
  // About the service, not the account.
  if (err.code === 'signup_disabled' || String(err.code || '').startsWith('hook_')) {
    return "Couldn't create an account right now. If this keeps happening, contact your school."
  }
  // "Already registered" and any unknown code share this, so neither is revealed.
  return "We couldn't create an account with those details. If you already have one, sign in instead."
}
