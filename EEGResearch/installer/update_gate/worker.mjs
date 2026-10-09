// The kit's download gate: the private bucket's feeds and Update installers, only for a request carrying the
// download key. What gets installed is the signed feed's decision (src/kit/update.py), never this Worker's.

const FEED = /^\/v1\/feed\/(latest|canary)\.json$/
const UPDATE = /^\/v1\/files\/(AdaptiveLearningSensors-Update-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe)$/

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

export default {
  async fetch(request, env) {
    if (request.method !== 'GET' && request.method !== 'HEAD') return answer(405, 'method not allowed', { Allow: 'GET, HEAD' })
    const expected = (env.DOWNLOAD_KEY ?? '').trim()  // `wrangler secret put` from a pipe keeps the newline
    if (!expected) return answer(503, 'the gate has no download key set')
    const auth = request.headers.get('Authorization') ?? ''
    // Missing and wrong get one answer, before any route: an unauthorised caller learns nothing of what exists.
    if (!auth.startsWith('Bearer ') || !sameKey(auth.slice(7), expected)) return answer(401, 'unauthorized')
    const path = new URL(request.url).pathname
    const feed = FEED.exec(path)
    const file = UPDATE.exec(path)
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
