// The kit's download gate: the private bucket's feeds and Update installers, only for a request carrying the
// download key. What gets installed is the signed feed's decision (src/kit/update.py), never this Worker's.
// The current Setup installer goes only to a link the backend signed for an admin (backend/kit_gate.py).

const FEED = /^\/v1\/feed\/(latest|canary)\.json$/
const UPDATE = /^\/v1\/files\/(AdaptiveLearningSensors-Update-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe)$/
const SETUP = /^\/v1\/setup\/current(\.json)?$/
const SETUP_FILE = /^AdaptiveLearningSensors-Setup-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe$/
const LINK_AHEAD_S = 15 * 60  // the backend signs for 10 minutes; the rest allows for clock skew

// Constant time over the expected key's length, so a response's timing says nothing of how much of a guess was right.
export function sameKey(given, expected) {
  const a = new TextEncoder().encode(given)
  const b = new TextEncoder().encode(expected)
  let diff = a.length ^ b.length
  for (let i = 0; i < b.length; i++) diff |= (a[i] ?? 0) ^ b[i]
  return b.length > 0 && diff === 0
}

function answer(status, text, headers = {}) {
  return new Response(text, { status, headers: { 'Cache-Control': 'no-store', ...headers } })
}

/** HMAC-SHA256 of `${kind}:${exp}` under the link secret, as hex: kind is "setup" (the file) or "meta" (its details). */
export async function linkSignature(secret, kind, exp) {
  const encode = (text) => new TextEncoder().encode(text)
  const key = await crypto.subtle.importKey('raw', encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, encode(`${kind}:${exp}`)))
  return Array.from(mac, (b) => b.toString(16).padStart(2, '0')).join('')
}

async function setup(request, env, url, kind) {
  const secret = (env.LINK_SECRET ?? '').trim()
  if (!secret) return answer(503, 'the gate has no link secret set')
  const exp = url.searchParams.get('exp') ?? ''
  const now = Math.floor(Date.now() / 1000)
  const fresh = /^\d{1,12}$/.test(exp) && Number(exp) > now && Number(exp) <= now + LINK_AHEAD_S
  // Expired and forged get one answer, before the bucket is asked.
  if (!fresh || !sameKey(url.searchParams.get('sig') ?? '', await linkSignature(secret, kind, exp))) {
    return answer(403, 'this link has expired or is not valid; ask the admin page for a new one')
  }
  const current = await env.KIT.get('setup/current.json')
  // Marked, so the backend can tell "nothing published" from a 404 that is not this gate's.
  if (!current) return answer(404, 'no installer has been published', { 'X-Kit-Setup': 'none' })
  const text = await current.text()
  if (kind === 'meta') {
    return answer(200, request.method === 'HEAD' ? null : text, { 'Content-Type': 'application/json' })
  }
  let file = null
  try { file = JSON.parse(text).file } catch { /* answered below */ }
  if (typeof file !== 'string' || !SETUP_FILE.test(file)) return answer(500, 'setup/current.json names no installer')
  const object = request.method === 'HEAD' ? await env.KIT.head(`setup/${file}`) : await env.KIT.get(`setup/${file}`)
  if (!object) return answer(500, `setup/current.json names ${file}, which is not in the bucket`)
  return answer(200, request.method === 'HEAD' ? null : object.body, {
    'Content-Length': String(object.size),
    'Content-Type': 'application/octet-stream',
    'Content-Disposition': `attachment; filename="${file}"`,
  })
}

export default {
  async fetch(request, env) {
    if (request.method !== 'GET' && request.method !== 'HEAD') return answer(405, 'method not allowed', { Allow: 'GET, HEAD' })
    const url = new URL(request.url)
    const setupRoute = SETUP.exec(url.pathname)
    // A browser following an admin's link sends no key; the link's own signature is the credential.
    if (setupRoute) return setup(request, env, url, setupRoute[1] ? 'meta' : 'setup')
    const expected = (env.DOWNLOAD_KEY ?? '').trim()  // `wrangler secret put` from a pipe keeps the newline
    if (!expected) return answer(503, 'the gate has no download key set')
    const auth = request.headers.get('Authorization') ?? ''
    // Missing and wrong get one answer, before any route: an unauthorised caller learns nothing of what exists.
    if (!auth.startsWith('Bearer ') || !sameKey(auth.slice(7), expected)) return answer(401, 'unauthorized')
    const feed = FEED.exec(url.pathname)
    const file = UPDATE.exec(url.pathname)
    if (!feed && !file) return answer(404, 'not found')
    const key = feed ? `feed/${feed[1]}.json` : `files/${file[1]}`
    const object = request.method === 'HEAD' ? await env.KIT.head(key) : await env.KIT.get(key)
    if (!object) return answer(404, 'not found')
    return new Response(request.method === 'HEAD' ? null : object.body, {
      headers: {
        'Cache-Control': 'no-store',
        'Content-Length': String(object.size),
        'Content-Type': feed ? 'application/json' : 'application/octet-stream',
      },
    })
  },
}
