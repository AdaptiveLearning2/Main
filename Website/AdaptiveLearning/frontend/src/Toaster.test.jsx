/** Exactly one `<Toaster />` in the app; a source check, since no test renders the real `main.jsx`. */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'

const SRC = resolve(fileURLToPath(import.meta.url), '..')

const walk = (dir) => readdirSync(dir).flatMap(name => {
  const full = join(dir, name)
  if (statSync(full).isDirectory()) return walk(full)
  return /\.jsx?$/.test(full) && !full.includes('.test.') ? [full] : []
})

describe('the notification container', () => {
  it('is mounted exactly once', () => {
    const mounts = walk(SRC)
      .filter(f => /<Toaster[\s/>]/.test(readFileSync(f, 'utf8')))
      .map(f => f.slice(SRC.length + 1).split(sep).join('/'))
    expect(mounts).toEqual(['components/ui/ThemedToaster.jsx'])
  })

  it('follows the app theme rather than the operating system', () => {
    // `system` follows the OS, not the app's `ThemeContext` toggle; assert its absence too.
    const src = readFileSync(
      resolve(SRC, 'components/ui/ThemedToaster.jsx'), 'utf8')
    expect(src).not.toMatch(/theme="system"/)
    expect(src).toMatch(/theme=\{dark \? 'dark' : 'light'\}/)
    expect(src).toMatch(/useTheme\(\)/)
    expect(src).toMatch(/richColors/)
    expect(src).toMatch(/closeButton/)
  })
})
