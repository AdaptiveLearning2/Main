// The gate against a fake bucket that records every key it is asked for. Run: node --test <this file>.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import worker, { linkSignature, sameKey } from './worker.mjs'

const KEY = 'k'.repeat(43)
const FILE = 'AdaptiveLearningSensors-Update-0.2.1.exe'

function bucket(objects) {
  const asked = []
  const find = (key) => {
    asked.push(key)
    return key in objects ? { body: objects[key], size: objects[key].length, text: async () => objects[key] } : null
  }
  return { asked, get: async (key) => find(key), head: async (key) => find(key) }
}

const stored = () => bucket({ 'feed/latest.json': '{"feed":1}', 'feed/canary.json': '{"feed":2}', [`files/${FILE}`]: 'exe!' })

async function call(path, { method = 'GET', auth = `Bearer ${KEY}`, kit = stored(), env } = {}) {
  const headers = auth === null ? {} : { Authorization: auth }
  const response = await worker.fetch(new Request(`https://kit-updates.example.workers.dev${path}`, { method, headers }),
    env ?? { KIT: kit, DOWNLOAD_KEY: KEY })
  return { response, kit, text: method === 'HEAD' ? null : await response.text() }
}

test('serves both feeds and an Update installer from their keys in the bucket', async () => {
  for (const [path, key, text] of [['/v1/feed/latest.json', 'feed/latest.json', '{"feed":1}'],
    ['/v1/feed/canary.json', 'feed/canary.json', '{"feed":2}'], [`/v1/files/${FILE}`, `files/${FILE}`, 'exe!']]) {
    const { response, kit, text: body } = await call(path)
    assert.equal(response.status, 200)
    assert.equal(body, text)
    assert.deepEqual(kit.asked, [key])
    assert.equal(response.headers.get('Content-Length'), String(text.length))
    assert.equal(response.headers.get('Cache-Control'), 'no-store')
  }
})

test('a missing key and a wrong one get the same 401, and the bucket is never asked', async () => {
  for (const auth of [null, '', `Bearer ${KEY}x`, `Bearer ${KEY.slice(1)}`, `bearer ${KEY}`, KEY, `Basic ${KEY}`]) {
    const { response, kit, text } = await call('/v1/feed/latest.json', { auth })
    assert.equal(response.status, 401, String(auth))
    assert.equal(text, 'unauthorized')
    assert.deepEqual(kit.asked, [])
  }
  const { response } = await call('/v1/files/nothing-here', { auth: null })
  assert.equal(response.status, 401)  // before routing: an unauthorised caller cannot map what exists
})

test('only the exact names are served', async () => {
  for (const path of ['/v1/feed/beta.json', '/v1/feed/latest.json/', '/v1/feed/latest', '/feed/latest.json',
    '/v1/files/AdaptiveLearningSensors-Setup-0.2.1.exe', `/v1/files/${FILE}.sha256`, `/v1/files/x/${FILE}`,
    '/v1/files/AdaptiveLearningSensors-Update-0.2.exe', '/v1/files/AdaptiveLearningSensors-Update-0.2.1.0.exe',
    '/v1/files/%2e%2e%2ffeed%2flatest.json', '/']) {
    const { response, kit } = await call(path)
    assert.equal(response.status, 404, path)
    assert.deepEqual(kit.asked, [], path)
  }
})

test('an object not in the bucket is a 404', async () => {
  const { response, kit } = await call('/v1/files/AdaptiveLearningSensors-Update-9.9.9.exe')
  assert.equal(response.status, 404)
  assert.deepEqual(kit.asked, ['files/AdaptiveLearningSensors-Update-9.9.9.exe'])
})

test('HEAD answers the headers with no body; other methods are refused untouched', async () => {
  const { response } = await call(`/v1/files/${FILE}`, { method: 'HEAD' })
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('Content-Length'), '4')
  assert.equal(response.body, null)
  for (const method of ['POST', 'PUT', 'DELETE']) {
    const { response: refused, kit } = await call('/v1/feed/latest.json', { method })
    assert.equal(refused.status, 405)
    assert.deepEqual(kit.asked, [])
  }
})

test('a key stored with a trailing newline still matches, and only the right key', async () => {
  const env = (kit) => ({ KIT: kit, DOWNLOAD_KEY: `${KEY}\n` })
  assert.equal((await call('/v1/feed/latest.json', { env: env(stored()) })).response.status, 200)
  const kit = stored()
  assert.equal((await call('/v1/feed/latest.json', { auth: `Bearer ${KEY}x`, env: env(kit) })).response.status, 401)
  assert.deepEqual(kit.asked, [])
})

test('with no download key set the gate serves nothing', async () => {
  for (const key of [undefined, '', '\n']) {
    const kit = stored()
    const { response } = await call('/v1/feed/latest.json', { auth: 'Bearer ', env: { KIT: kit, DOWNLOAD_KEY: key } })
    assert.equal(response.status, 503)
    assert.deepEqual(kit.asked, [])
  }
})

test('sameKey', () => {
  assert.equal(sameKey(KEY, KEY), true)
  assert.equal(sameKey(`${KEY}x`, KEY), false)
  assert.equal(sameKey(KEY.slice(1), KEY), false)
  assert.equal(sameKey('', ''), false)
  assert.equal(sameKey('', KEY), false)
})

// ─── Setup installers, behind a link the backend signs ──────────────────────────────────────────────────────────────

const SECRET = 'link-secret-for-tests'
const NOW = 1791599400  // a link signed now expires at EXP, ten minutes on
const EXP = 1791600000
const SETUP = 'AdaptiveLearningSensors-Setup-0.2.1.exe'
const CURRENT = JSON.stringify({ version: '0.2.1', file: SETUP, sha256: 'ab'.repeat(32), size: 5, published: '2026-10-09' })
const published = () => bucket({ 'setup/current.json': CURRENT, [`setup/${SETUP}`]: 'setup' })

async function at(seconds, run) {
  const real = Date.now
  Date.now = () => seconds * 1000
  try { return await run() } finally { Date.now = real }
}

// file null: the details link; otherwise that file's link.
async function signed(file, { exp = EXP, sig, kit = published(), env, now = NOW, method = 'GET', path } = {}) {
  const subject = file ? `setup:${file}` : 'meta'
  const query = `?exp=${exp}&sig=${sig ?? await linkSignature(SECRET, subject, exp)}`
  const target = path ?? (file ? `/v1/setup/files/${file}` : '/v1/setup/current.json')
  return at(now, () => call(target + query, { method, auth: null, kit, env: env ?? { KIT: kit, LINK_SECRET: SECRET } }))
}

test('the link signature is the one the backend computes: HMAC-SHA256 of "subject:exp", in hex', async () => {
  // The same vectors are asserted in backend/tests/test_kit_gate.py and tests/test_kit_update.py.
  assert.equal(await linkSignature(SECRET, `setup:${SETUP}`, EXP), '823bd77fcb80967df8ccf2f4d7a12d7fe0fb1aa5f236b703d29829a90f4c3e88')
  assert.equal(await linkSignature(SECRET, 'meta', EXP), '8e145db21708e6554a4d8c1f5cc471620d424a936d7dec1aa957327f389a498f')
})

test('a signed link downloads the file it names as an attachment, with no download key', async () => {
  const { response, kit, text } = await signed(SETUP)
  assert.equal(response.status, 200)
  assert.equal(text, 'setup')
  assert.deepEqual(kit.asked, [`setup/${SETUP}`])  // never current.json: a later publish cannot change what it serves
  assert.equal(response.headers.get('Content-Disposition'), `attachment; filename="${SETUP}"`)
  assert.equal(response.headers.get('Content-Type'), 'application/octet-stream')
  assert.equal(response.headers.get('Content-Length'), '5')
  assert.equal(response.headers.get('Cache-Control'), 'no-store')
})

test('a signed details link answers current.json as published, and asks for nothing else', async () => {
  const { response, kit, text } = await signed(null)
  assert.equal(response.status, 200)
  assert.equal(text, CURRENT)
  assert.deepEqual(kit.asked, ['setup/current.json'])
  assert.equal(response.headers.get('Content-Type'), 'application/json')
})

test('a link is honoured until it expires, and never more than fifteen minutes ahead', async () => {
  assert.equal((await signed(SETUP, { now: EXP - 1 })).response.status, 200)
  assert.equal((await signed(SETUP, { now: EXP - 900 })).response.status, 200)
  for (const now of [EXP, EXP + 1, EXP - 901]) {
    const { response, kit, text } = await signed(SETUP, { now })
    assert.equal(response.status, 403, String(now))
    // Apart from "refused", so a backend whose clock is off is not told its secret is wrong.
    assert.equal(response.headers.get('X-Kit-Setup'), 'expired')
    assert.match(text, /has expired/)
    assert.deepEqual(kit.asked, [])
  }
})

test('an expired link is judged before its signature, so a stale forgery learns nothing of the secret', async () => {
  const { response } = await signed(SETUP, { now: EXP, sig: 'f'.repeat(64) })
  assert.equal(response.headers.get('X-Kit-Setup'), 'expired')
})

test('a forged, altered or missing signature gets the one marked 403, and the bucket is never asked', async () => {
  const right = await linkSignature(SECRET, `setup:${SETUP}`, EXP)
  const cases = [
    { sig: '' }, { sig: right.toUpperCase() }, { sig: `${right}0` }, { sig: right.slice(1) },
    { sig: await linkSignature(SECRET, 'meta', EXP) },
    { sig: await linkSignature(SECRET, 'setup:AdaptiveLearningSensors-Setup-0.2.2.exe', EXP) },
    { sig: await linkSignature('another-secret', `setup:${SETUP}`, EXP) },
    { exp: EXP + 1, sig: right }, { exp: `${EXP}.0` }, { exp: '' }, { exp: '-1' },
  ]
  for (const c of cases) {
    const { response, kit, text } = await signed(SETUP, c)
    assert.equal(response.status, 403, JSON.stringify(c))
    assert.equal(response.headers.get('X-Kit-Setup'), 'refused')
    assert.match(text, /is not valid/)
    assert.deepEqual(kit.asked, [], JSON.stringify(c))
  }
})

test('a link signed for one file opens no other', async () => {
  const kit = bucket({ [`setup/${SETUP}`]: 'setup', 'setup/AdaptiveLearningSensors-Setup-0.2.2.exe': 'newer' })
  const sig = await linkSignature(SECRET, `setup:${SETUP}`, EXP)
  // It opens its own file first: a gate refusing everything would pass the refusal alone.
  assert.equal((await signed(SETUP, { sig, kit })).response.status, 200)
  const { response } = await signed('AdaptiveLearningSensors-Setup-0.2.2.exe', { sig, kit })
  assert.equal(response.status, 403)
  assert.deepEqual(kit.asked, [`setup/${SETUP}`])
})

test('the download key is not a way round the signature', async () => {
  const kit = published()
  const env = { KIT: kit, DOWNLOAD_KEY: KEY, LINK_SECRET: SECRET }
  for (const path of ['/v1/setup/current.json', `/v1/setup/files/${SETUP}`]) {
    const { response } = await at(NOW, () => call(path, { kit, env }))
    assert.equal(response.status, 403, path)
  }
  assert.deepEqual(kit.asked, [])
})

test('nothing published and a file not in the bucket are 404s marked as the gate\'s own', async () => {
  const none = await signed(null, { kit: bucket({}) })
  assert.equal(none.response.status, 404)
  assert.equal(none.response.headers.get('X-Kit-Setup'), 'none')
  const missing = await signed(SETUP, { kit: bucket({ 'setup/current.json': CURRENT }) })
  assert.equal(missing.response.status, 404)
  assert.equal(missing.response.headers.get('X-Kit-Setup'), 'missing')
})

test('with no link secret set no link is honoured, and the 503 says it is the gate\'s', async () => {
  for (const secret of [undefined, '', '\n']) {
    const kit = published()
    const { response } = await signed(SETUP, { kit, env: { KIT: kit, LINK_SECRET: secret } })
    assert.equal(response.status, 503)
    assert.equal(response.headers.get('X-Kit-Setup'), 'no-secret')
    assert.deepEqual(kit.asked, [])
  }
})

test('HEAD on a file link answers its size with no body, as the backend checks it', async () => {
  const { response, kit } = await signed(SETUP, { method: 'HEAD' })
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('Content-Length'), '5')
  assert.equal(response.body, null)
  assert.deepEqual(kit.asked, [`setup/${SETUP}`])
})

test('only the exact Setup paths skip the download key', async () => {
  for (const path of ['/v1/setup/current', '/v1/setup/current.json/', '/v1/setup/current.json.exe', `/v1/setup/${SETUP}`,
    `/v1/setup/files/${SETUP}/`, `/v1/setup/files/${FILE}`, '/v1/setup/files/AdaptiveLearningSensors-Setup-0.2.exe',
    `/v1/setup/files/x/${SETUP}`, '/v1/setup/', '/v1/setup']) {
    const { response } = await at(NOW, () => call(`${path}?exp=${EXP}&sig=x`, { auth: null }))
    assert.equal(response.status, 401, path)
  }
})
