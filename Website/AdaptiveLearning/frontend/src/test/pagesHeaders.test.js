// @vitest-environment node
/** The Pages headers: what the CSP allows, and the plugin hooks that emit and serve it. */
import { describe, it, expect, vi } from 'vitest'
import { Buffer } from 'node:buffer'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ASSET_CACHE, headersFile, pagesHeaders, pagesHeadersPlugin, parseHeadersFile,
         renderHeadersFile } from '../../pagesHeaders.js'
import { DEFAULT_SIDECAR_URL } from '../lib/origins.js'
import viteConfig from '../../vite.config.js'

const ENV = {
  VITE_SUPABASE_URL: 'https://abcd.supabase.co/',
  VITE_API_URL: 'https://api.example.org/v1',
  VITE_EEG_LOCAL_URL: 'http://127.0.0.1:8001',
}

const directives = csp => Object.fromEntries(csp.split('; ').map(d => {
  const [name, ...sources] = d.split(' ')
  return [name, sources]
}))

const cspOf = env => directives(pagesHeaders(env)['Content-Security-Policy'])

it('lets the page reach exactly its API, Supabase and the local sidecar', () => {
  expect(cspOf(ENV)['connect-src']).toEqual(
    ["'self'", 'https://api.example.org', 'https://abcd.supabase.co', 'http://127.0.0.1:8001'])
  expect(cspOf(ENV)['img-src']).toEqual(["'self'", 'https://abcd.supabase.co'])
})

it("allows the sidecar default the page falls back to, since it is the student's own machine", () => {
  const { VITE_EEG_LOCAL_URL: _unset, ...env } = ENV
  expect(cspOf(env)['connect-src']).toContain(new URL(DEFAULT_SIDECAR_URL).origin)
})

it('runs only its own scripts, and never upgrades the plain-HTTP sidecar call', () => {
  const csp = pagesHeaders(ENV)['Content-Security-Policy']
  expect(directives(csp)['script-src']).toEqual(["'self'"])
  expect(csp).not.toMatch(/unsafe-eval|upgrade-insecure-requests|\*/)
  expect(directives(csp)['frame-ancestors']).toEqual(["'none'"])
})

it('serves Inter itself, so no stylesheet or font comes from a third party', () => {
  // Google Fonts cost first paint a third-party round trip, and sent every pupil's IP to Google.
  expect(cspOf(ENV)['style-src']).toEqual(["'self'", "'unsafe-inline'"])
  expect(cspOf(ENV)['font-src']).toEqual(["'self'"])
})

it('never inlines a font as a data: URI, which that font-src would block', () => {
  // Vite inlines any asset under 4 KiB, and a small font subset can be one.
  const inline = viteConfig.build?.assetsInlineLimit
  expect(inline).toBeTypeOf('function')
  for (const file of ['/x/inter-latin-wght-normal.woff2', '/x/a.woff', '/x/a.ttf', '/x/a.otf', '/x/A.WOFF2']) {
    expect(inline(file, Buffer.alloc(10))).toBe(false)
  }
  // Anything else keeps Vite's own size rule.
  expect(inline('/x/icon.png', Buffer.alloc(10))).toBeUndefined()
})

it.each(['VITE_SUPABASE_URL', 'VITE_API_URL'])('refuses a build without %s', name => {
  // Missing, the bundle would call localhost (the API) or nothing (Supabase) from every student's browser.
  const env = { ...ENV, [name]: '' }
  expect(() => pagesHeaders(env)).toThrow(name)
})

it('reads back exactly the headers it writes', () => {
  expect(parseHeadersFile(headersFile(ENV))).toEqual(pagesHeaders(ENV))
})

it('caches the hashed assets for a year, and leaves every other path revalidating', () => {
  // Pages' default for everything is max-age=0, so every chunk was re-asked on each visit.
  const file = headersFile(ENV)
  expect(parseHeadersFile(file, '/assets/*')).toEqual({ 'Cache-Control': ASSET_CACHE })
  expect(ASSET_CACHE).toMatch(/max-age=31536000/)
  expect(ASSET_CACHE).toMatch(/immutable/)
  // On `/*` it would pin index.html, so a deploy would not be seen.
  expect(parseHeadersFile(file)).not.toHaveProperty('Cache-Control')
})

it('refuses a header two blocks both set, since Pages and vite preview would disagree on its value', () => {
  // Pages comma-joins the two on a path both blocks match; preview keeps the later one.
  const blocks = { '/*': { 'Cache-Control': 'no-cache' }, '/assets/*': { 'cache-control': ASSET_CACHE } }
  expect(() => renderHeadersFile(blocks)).toThrow('cache-control is set by both /* and /assets/*')
  expect(() => headersFile(ENV)).not.toThrow()
})

describe('the Vite plugin', () => {
  it('is registered in the app config', () => {
    expect(viteConfig.plugins.flat().map(p => p?.name)).toContain('pages-headers')
  })

  it('emits _headers into the build', () => {
    const plugin = pagesHeadersPlugin()
    plugin.configResolved({ env: ENV })
    const emitFile = vi.fn()
    plugin.generateBundle.call({ emitFile })
    expect(emitFile).toHaveBeenCalledWith({ type: 'asset', fileName: '_headers', source: headersFile(ENV) })
  })

  it('emits a 404 page beside the hashed assets, so a missing chunk is a real 404, never cached', () => {
    // Without it Pages answers a missing /assets/ file with index.html, a 200, under the year-long cache.
    const plugin = pagesHeadersPlugin()
    plugin.configResolved({ env: ENV })
    const emitFile = vi.fn()
    plugin.generateBundle.call({ emitFile })
    const page = emitFile.mock.calls.map(([file]) => file).find(file => file.fileName.endsWith('404.html'))
    expect(page).toMatchObject({ type: 'asset', fileName: 'assets/404.html' })
    expect(page.source).toMatch(/<title>Not found<\/title>/)
    // In the cached block's directory: a top-level 404.html would end the SPA fallback for every route.
    const dir = `/${page.fileName.slice(0, page.fileName.lastIndexOf('/'))}/*`
    expect(parseHeadersFile(headersFile(ENV), dir)).toEqual({ 'Cache-Control': ASSET_CACHE })
  })

  it('makes vite preview serve the built file, not a policy rebuilt from the current env', () => {
    const root = mkdtempSync(join(tmpdir(), 'pages-headers-'))
    try {
      mkdirSync(join(root, 'dist'))
      writeFileSync(join(root, 'dist', '_headers'), '/*\n  Content-Security-Policy: built-value\n')
      const plugin = pagesHeadersPlugin()
      plugin.configResolved({ env: ENV, root, build: { outDir: 'dist' } })
      let middleware
      plugin.configurePreviewServer({ middlewares: { use: fn => { middleware = fn } } })

      const setHeader = vi.fn()
      const next = vi.fn()
      middleware({}, { setHeader }, next)

      expect(setHeader.mock.calls).toEqual([['Content-Security-Policy', 'built-value']])
      expect(next).toHaveBeenCalled()
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('makes vite preview add the asset block on /assets/ paths only', () => {
    const root = mkdtempSync(join(tmpdir(), 'pages-headers-'))
    try {
      mkdirSync(join(root, 'dist'))
      writeFileSync(join(root, 'dist', '_headers'),
        '/*\n  X-Frame-Options: DENY\n\n/assets/*\n  Cache-Control: built-cache\n')
      const plugin = pagesHeadersPlugin()
      plugin.configResolved({ env: ENV, root, build: { outDir: 'dist' } })
      let middleware
      plugin.configurePreviewServer({ middlewares: { use: fn => { middleware = fn } } })

      const asset = vi.fn()
      middleware({ url: '/assets/index-abc.js' }, { setHeader: asset }, () => {})
      expect(asset.mock.calls).toEqual([['X-Frame-Options', 'DENY'], ['Cache-Control', 'built-cache']])

      const page = vi.fn()
      middleware({ url: '/teacher' }, { setHeader: page }, () => {})
      expect(page.mock.calls).toEqual([['X-Frame-Options', 'DENY']])
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('says to build first when preview finds no _headers', () => {
    const root = mkdtempSync(join(tmpdir(), 'pages-headers-'))
    try {
      const plugin = pagesHeadersPlugin()
      plugin.configResolved({ env: ENV, root, build: { outDir: 'dist' } })
      expect(() => plugin.configurePreviewServer({ middlewares: { use: () => {} } }))
        .toThrow(/run vite build first/)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
