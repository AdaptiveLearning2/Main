// Response headers for the Cloudflare Pages build: emitted as `dist/_headers`, and applied by
// `vite preview` so a local check sees the same policy. HSTS and the HTTPS redirect are
// Cloudflare zone settings, not here (CLAUDE.md, "The network edge").
import { DEFAULT_API_URL, DEFAULT_SIDECAR_URL } from './src/lib/origins.js'

const origin = url => new URL(url).origin

/** Headers every page response carries, for the build's `VITE_*` env. */
export function pagesHeaders(env) {
  if (!env.VITE_SUPABASE_URL) throw new Error('pagesHeaders: VITE_SUPABASE_URL is not set')
  const supabase = origin(env.VITE_SUPABASE_URL)
  const api = origin(env.VITE_API_URL || DEFAULT_API_URL)
  const sidecar = origin(env.VITE_EEG_LOCAL_URL || DEFAULT_SIDECAR_URL)
  const csp = [
    "default-src 'self'",
    "script-src 'self'",
    // sonner and framer-motion insert <style> elements at runtime; index.css imports Inter from Google Fonts.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    // Archived charts are <img> tags on signed Supabase Storage URLs.
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

/** Emits `_headers` on build and sets the same headers on `vite preview`. */
export function pagesHeadersPlugin() {
  let env
  return {
    name: 'pages-headers',
    configResolved(config) { env = config.env },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: '_headers', source: headersFile(env) })
    },
    configurePreviewServer(server) {
      const headers = pagesHeaders(env)
      server.middlewares.use((_req, res, next) => {
        for (const [k, v] of Object.entries(headers)) res.setHeader(k, v)
        next()
      })
    },
  }
}
