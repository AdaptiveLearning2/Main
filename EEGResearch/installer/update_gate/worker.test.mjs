// The gate against a fake bucket that records every key it is asked for. Run: node --test <this file>.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import worker, { sameKey } from './worker.mjs'

const KEY = 'k'.repeat(43)
const FILE = 'AdaptiveLearningSensors-Update-0.2.1.exe'

function bucket(objects) {
  const asked = []
  const find = (key) => {
    asked.push(key)
    return key in objects ? { body: objects[key], size: objects[key].length } : null
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
