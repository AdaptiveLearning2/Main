/** Fixtures are the error classes auth-js actually throws (`AuthApiError`, `AuthRetryableFetchError`). */
import { describe, it, expect } from 'vitest'
import { AuthApiError, AuthRetryableFetchError, AuthWeakPasswordError } from '@supabase/supabase-js'
import { signInMessage, signUpMessage } from './authErrors'

const refused = (message, status, code) => new AuthApiError(message, status, code)

describe('sign-in', () => {
  it('says the same thing for an unknown email and a wrong password', () => {
    // "No such user" must not get a sentence of its own.
    const wrong   = signInMessage(refused('Invalid login credentials', 400, 'invalid_credentials'))
    const unknown = signInMessage(refused('User not found', 400, 'user_not_found'))

    expect(wrong).toBe('Email or password is incorrect.')
    expect(unknown).toBe(wrong)
  })

  it.each([
    ['no answer at all', new AuthRetryableFetchError('Failed to fetch', 0)],
    ['a gateway failure', new AuthRetryableFetchError('Bad Gateway', 502)],
    ['a server error', refused('Internal error', 500, 'unexpected_failure')],
  ])('does not call %s a wrong password', (_name, err) => {
    // "Incorrect" for an outage sends someone to reset a password that is fine.
    expect(signInMessage(err)).toMatch(/couldn't reach/i)
  })

  it('says to wait when throttled', () => {
    expect(signInMessage(refused('Request rate limit reached', 429, 'over_request_rate_limit')))
      .toMatch(/too many attempts/i)
  })

  it('says to confirm the email, which only the right password reaches', () => {
    expect(signInMessage(refused('Email not confirmed', 400, 'email_not_confirmed')))
      .toMatch(/confirm your email/i)
  })

  it('reads a refusal with no code as bad credentials, as an older server means it', () => {
    expect(signInMessage(refused('Invalid login credentials', 400, undefined)))
      .toBe('Email or password is incorrect.')
  })

  it.each([
    ['a banned account', refused('User is banned', 400, 'user_banned')],
    ['a failed captcha', refused('captcha protection', 400, 'captcha_failed')],
    ['a hook refusing', refused('Hook timed out', 422, 'hook_timeout')],
  ])('does not call %s a wrong password', (_name, err) => {
    // "Incorrect" sends someone to reset a password that is fine.
    expect(signInMessage(err)).not.toMatch(/incorrect/i)
  })

  it('points everyone at the school, not at a teacher', () => {
    // Parents and teachers sign in here too.
    const msg = signInMessage(refused('User is banned', 400, 'user_banned'))
    expect(msg).toMatch(/contact your school/i)
    expect(msg).not.toMatch(/teacher/i)
  })
})

describe('sign-up', () => {
  it('does not say that an address is already registered', () => {
    const taken = signUpMessage(refused('User already registered', 422, 'user_already_exists'))

    expect(taken).not.toMatch(/already registered|already exists|in use|taken/i)
    // One sentence for every refusal about the account.
    expect(signUpMessage(refused('Email exists', 422, 'email_exists'))).toBe(taken)
    // And a code nobody has seen yet, so a new spelling of "taken" is covered.
    expect(signUpMessage(refused('Something new', 422, 'not_yet_invented'))).toBe(taken)
  })

  it.each([
    ['a failed captcha', refused('captcha protection', 400, 'captcha_failed'), /verification check/i],
    ['a hook refusing', refused('Hook timed out', 422, 'hook_timeout'), /right now/i],
    ['sign-ups switched off', refused('Signups not allowed', 422, 'signup_disabled'), /right now/i],
  ])('does not answer %s with "sign in instead"', (_name, err, expected) => {
    // Not about the account, so nothing is given away by saying what happened.
    const msg = signUpMessage(err)
    expect(msg).toMatch(expected)
    expect(msg).not.toMatch(/already have one/i)
  })

  it('says which password rule failed, when Supabase says', () => {
    // Production rules can be stricter than the page's 6-character check.
    const msg = signUpMessage(new AuthWeakPasswordError('Password is weak', 422, ['length', 'pwned']))

    expect(msg).toMatch(/too short/i)
    expect(msg).toMatch(/data breach/i)
    expect(msg).not.toMatch(/kinds of character/i)
  })

  it('still says what is wrong with the input', () => {
    // About the password or the address typed, not about an account.
    expect(signUpMessage(refused('Password is too weak', 422, 'weak_password')))
      .toBe('Choose a stronger password.')
    expect(signUpMessage(refused('Invalid email', 400, 'email_address_invalid')))
      .toMatch(/valid email/i)
  })

  it('does not blame the details for an outage', () => {
    expect(signUpMessage(new AuthRetryableFetchError('Failed to fetch', 0))).toMatch(/couldn't reach/i)
  })

  it('says to wait when throttled', () => {
    expect(signUpMessage(refused('Email rate limit exceeded', 429, 'over_email_send_rate_limit')))
      .toMatch(/too many attempts/i)
  })
})

it('never shows Supabase\'s own message', () => {
  const marker = 'SUPABASE-SENTENCE'
  for (const err of [
    refused(marker, 400, 'invalid_credentials'),
    refused(marker, 422, 'user_already_exists'),
    refused(marker, 422, 'weak_password'),
    new AuthRetryableFetchError(marker, 0),
  ]) {
    expect(signInMessage(err)).not.toContain(marker)
    expect(signUpMessage(err)).not.toContain(marker)
  }
})
