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
    // sonner and framer-motion insert <style> elements at runtime. Inter is bundled, so fonts are 'self'.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
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

// Vite content-hashes every file under /assets/, so a name never serves two contents.
// index.html keeps Pages' revalidating default, so a deploy is seen on the next load.
export const ASSET_CACHE = 'public, max-age=31536000, immutable'

// A missing /assets/ file would get Pages' SPA fallback (index.html, 200) under that year-long cache.
// The nearest 404.html wins over the fallback, and Pages sends every 404 as no-store.
export const MISSING_ASSET_PAGE = {
  fileName: 'assets/404.html',
  source: '<!doctype html>\n<meta charset="utf-8">\n<title>Not found</title>\n',
}

/** Every `_headers` block, by path pattern: every path's policy, then the hashed assets'. */
export function headerBlocks(env) {
  return { '/*': pagesHeaders(env), '/assets/*': { 'Cache-Control': ASSET_CACHE } }
}

/**
 * Renders blocks as a `_headers` file. Refuses a header named in two blocks: Pages would
 * comma-join the two values where both match, and `vite preview` would keep only one.
 */
export function renderHeadersFile(blocks) {
  const setBy = new Map()
  for (const [path, headers] of Object.entries(blocks)) {
    for (const name of Object.keys(headers)) {
      const key = name.toLowerCase()
      if (setBy.has(key)) throw new Error(`pagesHeaders: ${name} is set by both ${setBy.get(key)} and ${path}`)
      setBy.set(key, path)
    }
  }
  return Object.entries(blocks)
    .flatMap(([path, headers]) => [path, ...Object.entries(headers).map(([k, v]) => `  ${k}: ${v}`), ''])
    .join('\n')
}

/** The `_headers` file Cloudflare Pages reads. */
export function headersFile(env) {
  return renderHeadersFile(headerBlocks(env))
}

/** Reads back one block of a `_headers` file (default `/*`), as `{name: value}`. */
export function parseHeadersFile(text, block = '/*') {
  const headers = {}
  let inBlock = false
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith(' ')) { inBlock = line.trim() === block; continue }
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
      this.emitFile({ type: 'asset', ...MISSING_ASSET_PAGE })
    },
    configurePreviewServer(server) {
      const file = resolve(config.root, config.build.outDir, '_headers')
      let text
      try { text = readFileSync(file, 'utf8') } catch {
        throw new Error(`pagesHeaders: no ${file}; run vite build first`)
      }
      const headers = parseHeadersFile(text)
      const assetHeaders = parseHeadersFile(text, '/assets/*')
      // Both blocks apply to an asset, as on Pages.
      server.middlewares.use((req, res, next) => {
        for (const [k, v] of Object.entries(headers)) res.setHeader(k, v)
        if (req.url?.startsWith('/assets/')) {
          for (const [k, v] of Object.entries(assetHeaders)) res.setHeader(k, v)
        }
        next()
      })
    },
  }
}
