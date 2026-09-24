/**
 * Built from the error classes auth-js actually throws, not from hand-made
 * objects: a refusal is an `AuthApiError(message, status, code)`, a request
 * that got no answer is an `AuthRetryableFetchError` with status 0 (or the
 * gateway's 502-504). A fixture of `{ message }` alone would pass against a
 * page that still printed Supabase's own sentence.
 */
import { describe, it, expect } from 'vitest'
import { AuthApiError, AuthRetryableFetchError } from '@supabase/supabase-js'
import { signInMessage, signUpMessage } from './authErrors'

const refused = (message, status, code) => new AuthApiError(message, status, code)

describe('sign-in', () => {
  it('says the same thing for an unknown email and a wrong password', () => {
    // Supabase already answers both with `invalid_credentials`; a code for
    // "no such user" must not get a sentence of its own either.
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
})

describe('sign-up', () => {
  it('does not say that an address is already registered', () => {
    const taken = signUpMessage(refused('User already registered', 422, 'user_already_exists'))

    expect(taken).not.toMatch(/already registered|already exists|in use|taken/i)
    // One sentence for every refusal about the account, so this one cannot be
    // told apart from the others by its wording.
    expect(signUpMessage(refused('Email exists', 422, 'email_exists'))).toBe(taken)
    expect(signUpMessage(refused('Signups not allowed', 422, 'signup_disabled'))).toBe(taken)
  })

  it('still says what is wrong with the input', () => {
    // About the password or the address typed, not about an account.
    expect(signUpMessage(refused('Password is too weak', 422, 'weak_password')))
      .toMatch(/stronger password/i)
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
