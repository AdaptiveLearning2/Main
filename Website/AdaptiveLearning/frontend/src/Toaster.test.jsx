/**
 * Exactly one `<Toaster />` in the app.
 *
 * There were two -- `App.jsx` at top-right and `main.jsx` at bottom-right --
 * so every single notification rendered twice, once in each corner. It looked
 * like a duplicated error rather than a duplicated container, which is why it
 * survived: the message was correct, there were just two of it, every time.
 *
 * A source check because nothing else can see it. Each Toaster is a valid
 * mount on its own, no test renders the real `main.jsx`, and a component test
 * that mounts one page sees neither -- so the fault only ever appears to a
 * person looking at the running app.
 */
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
    expect(mounts).toEqual(['main.jsx'])
  })

  it('follows the app theme, which the removed one did not', () => {
    // The two mounts had different props; keeping either wholesale would have
    // lost something. This one carries the union.
    const src = readFileSync(resolve(SRC, 'main.jsx'), 'utf8')
    expect(src).toMatch(/theme="system"/)
    expect(src).toMatch(/richColors/)
    expect(src).toMatch(/closeButton/)
  })
})
