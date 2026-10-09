// The kit's download gate: the private bucket's feeds and Update installers, only for a request carrying the
// download key. What gets installed is the signed feed's decision (src/kit/update.py), never this Worker's.
// Setup installers go only to a link the backend signed for an admin (backend/kit_gate.py).

const FEED = /^\/v1\/feed\/(latest|canary)\.json$/
const UPDATE = /^\/v1\/files\/(AdaptiveLearningSensors-Update-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe)$/
const SETUP_META = /^\/v1\/setup\/current\.json$/
const SETUP_FILE = /^\/v1\/setup\/files\/(AdaptiveLearningSensors-Setup-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe)$/
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

/** HMAC-SHA256 of `${subject}:${exp}` in hex. The subject is "meta", or "setup:<file>", so a link is one file's. */
export async function linkSignature(secret, subject, exp) {
  const encode = (text) => new TextEncoder().encode(text)
  const key = await crypto.subtle.importKey('raw', encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, encode(`${subject}:${exp}`)))
  return Array.from(mac, (b) => b.toString(16).padStart(2, '0')).join('')
}

// Each of the gate's own refusals is marked, so the backend can name the fix instead of offering a retry.
const marked = (status, text, mark) => answer(status, text, { 'X-Kit-Setup': mark })

async function setup(request, env, url, file) {
  const secret = (env.LINK_SECRET ?? '').trim()
  if (!secret) return marked(503, 'the gate has no link secret set', 'no-secret')
  const exp = url.searchParams.get('exp') ?? ''
  const now = Math.floor(Date.now() / 1000)
  const fresh = /^\d{1,12}$/.test(exp) && Number(exp) > now && Number(exp) <= now + LINK_AHEAD_S
  const subject = file ? `setup:${file}` : 'meta'
  // Expired and forged get one answer, before the bucket is asked.
  if (!fresh || !sameKey(url.searchParams.get('sig') ?? '', await linkSignature(secret, subject, exp))) {
    return marked(403, 'this link has expired or is not valid; ask the admin page for a new one', 'refused')
  }
  const head = request.method === 'HEAD'
  if (!file) {
    const current = await env.KIT.get('setup/current.json')
    if (!current) return marked(404, 'no installer has been published', 'none')
    return answer(200, head ? null : await current.text(), { 'Content-Type': 'application/json' })
  }
  const object = head ? await env.KIT.head(`setup/${file}`) : await env.KIT.get(`setup/${file}`)
  if (!object) return marked(404, `${file} is not in the bucket`, 'missing')
  return answer(200, head ? null : object.body, {
    'Content-Length': String(object.size),
    'Content-Type': 'application/octet-stream',
    'Content-Disposition': `attachment; filename="${file}"`,
  })
}

export default {
  async fetch(request, env) {
    if (request.method !== 'GET' && request.method !== 'HEAD') return answer(405, 'method not allowed', { Allow: 'GET, HEAD' })
    const url = new URL(request.url)
    // A browser following an admin's link sends no key; the link's own signature is the credential.
    if (SETUP_META.test(url.pathname)) return setup(request, env, url, null)
    const setupFile = SETUP_FILE.exec(url.pathname)
    if (setupFile) return setup(request, env, url, setupFile[1])
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
