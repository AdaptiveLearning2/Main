// Response headers for the Cloudflare Pages build: emitted as `dist/_headers`, which `vite preview`
// then serves as built. HSTS and the HTTPS redirect are Cloudflare zone settings, not here
// (CLAUDE.md, "The network edge").
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { DEFAULT_SIDECAR_URL } from './src/lib/origins.js'

const origin = url => new URL(url).origin

// A build missing either would ship a bundle calling localhost or nothing, so it fails instead.
const REQUIRED = ['VITE_SUPABASE_URL', 'VITE_API_URL']

/** Headers every page response carries, for the build's `VITE_*` env. */
export function pagesHeaders(env) {
  for (const name of REQUIRED) {
    if (!env[name]) throw new Error(`pagesHeaders: ${name} is not set`)
  }
  const supabase = origin(env.VITE_SUPABASE_URL)
  const api = origin(env.VITE_API_URL)
  // The sidecar is on the student's own machine, so its loopback default is right in production.
  const sidecar = origin(env.VITE_EEG_LOCAL_URL || DEFAULT_SIDECAR_URL)
  const csp = [
    "default-src 'self'",
    "script-src 'self'",
    // sonner and framer-motion insert <style> elements at runtime; index.css imports Inter from Google Fonts.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    // SessionReview (/teacher/sessions/:id) shows archived charts as <img> on signed Storage URLs.
    `img-src 'self' ${supabase}`,
    `connect-src 'self' ${api} ${supabase} ${sidecar}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ]
  return {
    'Content-Security-Policy': csp.join('; '),
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    // The webcam is opened by the sidecar, never by this page.
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
  }
}

/** The `_headers` file Cloudflare Pages reads: one block applying to every path. */
export function headersFile(env) {
  const lines = Object.entries(pagesHeaders(env)).map(([k, v]) => `  ${k}: ${v}`)
  return ['/*', ...lines, ''].join('\n')
}

/** Reads back the `/*` block of a `_headers` file, as `{name: value}`. */
export function parseHeadersFile(text) {
  const headers = {}
  let inBlock = false
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith(' ')) { inBlock = line.trim() === '/*'; continue }
    const at = line.indexOf(':')
    if (inBlock && at > 0) headers[line.slice(0, at).trim()] = line.slice(at + 1).trim()
  }
  return headers
}

/** Emits `_headers` on build; `vite preview` serves that built file, never a fresh policy. */
export function pagesHeadersPlugin() {
  let config
  return {
    name: 'pages-headers',
    configResolved(resolved) { config = resolved },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: '_headers', source: headersFile(config.env) })
    },
    configurePreviewServer(server) {
      const file = resolve(config.root, config.build.outDir, '_headers')
      let text
      try { text = readFileSync(file, 'utf8') } catch {
        throw new Error(`pagesHeaders: no ${file}; run vite build first`)
      }
      const headers = parseHeadersFile(text)
      server.middlewares.use((_req, res, next) => {
        for (const [k, v] of Object.entries(headers)) res.setHeader(k, v)
        next()
      })
    },
  }
}
