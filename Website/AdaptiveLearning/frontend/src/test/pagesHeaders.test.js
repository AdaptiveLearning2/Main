// @vitest-environment node
/** The Pages headers: what the CSP allows, and that a real build and preview carry it. */
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import process from 'node:process'
import { build, preview } from 'vite'
import { pagesHeaders } from '../../pagesHeaders.js'
import { DEFAULT_API_URL, DEFAULT_SIDECAR_URL } from '../lib/origins.js'

const ENV = {
  VITE_SUPABASE_URL: 'https://abcd.supabase.co/',
  VITE_API_URL: 'https://api.example.org/v1',
  VITE_EEG_LOCAL_URL: 'http://127.0.0.1:8001',
}

const directives = csp => Object.fromEntries(csp.split('; ').map(d => {
  const [name, ...sources] = d.split(' ')
  return [name, sources]
}))

it('lets the page reach exactly its API, Supabase and the local sidecar', () => {
  const d = directives(pagesHeaders(ENV)['Content-Security-Policy'])
  expect(d['connect-src']).toEqual(
    ["'self'", 'https://api.example.org', 'https://abcd.supabase.co', 'http://127.0.0.1:8001'])
  expect(d['img-src']).toEqual(["'self'", 'https://abcd.supabase.co'])
})

it('allows the same defaults the page falls back to when the env names none', () => {
  const d = directives(pagesHeaders({ VITE_SUPABASE_URL: ENV.VITE_SUPABASE_URL })['Content-Security-Policy'])
  expect(d['connect-src']).toContain(new URL(DEFAULT_API_URL).origin)
  expect(d['connect-src']).toContain(new URL(DEFAULT_SIDECAR_URL).origin)
})

it('runs only its own scripts, and never upgrades the plain-HTTP sidecar call', () => {
  const csp = pagesHeaders(ENV)['Content-Security-Policy']
  expect(directives(csp)['script-src']).toEqual(["'self'"])
  expect(csp).not.toMatch(/unsafe-eval|upgrade-insecure-requests|\*/)
  expect(directives(csp)['frame-ancestors']).toEqual(["'none'"])
})

it('loads the Inter font index.css imports, stylesheet and files', () => {
  const d = directives(pagesHeaders(ENV)['Content-Security-Policy'])
  expect(d['style-src']).toContain('https://fonts.googleapis.com')
  expect(d['font-src']).toEqual(["'self'", 'https://fonts.gstatic.com'])
})

it('refuses to build a policy without the Supabase origin', () => {
  expect(() => pagesHeaders({})).toThrow(/VITE_SUPABASE_URL/)
})

describe('a real build', () => {
  const root = resolve(import.meta.dirname, '../..')
  const saved = {}
  let outDir

  beforeAll(async () => {
    for (const [k, v] of Object.entries(ENV)) { saved[k] = process.env[k]; process.env[k] = v }
    outDir = mkdtempSync(join(tmpdir(), 'pages-headers-'))
    await build({ root, logLevel: 'silent', build: { outDir, emptyOutDir: true } })
  }, 120_000)

  afterAll(() => {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v
    }
    rmSync(outDir, { recursive: true, force: true })
  })

  it('writes _headers applying the policy to every path', () => {
    const file = readFileSync(join(outDir, '_headers'), 'utf8')
    expect(file.split('\n')[0]).toBe('/*')
    expect(file).toContain(`  Content-Security-Policy: ${pagesHeaders(ENV)['Content-Security-Policy']}`)
  })

  it('serves the same headers from vite preview', async () => {
    const server = await preview({ root, logLevel: 'silent', build: { outDir }, preview: { port: 0 } })
    try {
      const res = await fetch(`http://localhost:${server.httpServer.address().port}/`)
      expect(res.headers.get('content-security-policy'))
        .toBe(pagesHeaders(ENV)['Content-Security-Policy'])
      expect(res.headers.get('x-content-type-options')).toBe('nosniff')
    } finally {
      await server.close()
    }
  }, 60_000)
})
