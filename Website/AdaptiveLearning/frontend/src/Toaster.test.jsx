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
    expect(mounts).toEqual(['components/ui/ThemedToaster.jsx'])
  })

  it('follows the app theme rather than the operating system', () => {
    // This previously asserted `theme="system"` under the name "follows the
    // app theme", which is the opposite of what that prop does. `system`
    // resolves from `prefers-color-scheme`; this app's theme is a manual
    // toggle stored in `al_theme` and applied as a `dark` class by
    // `ThemeContext`. The two agree only when the OS happens to match the
    // toggle, so a student who chose dark on a light laptop got light toasts
    // over a dark page -- and this test named the bug as the fix.
    //
    // The absence of `system` is asserted as well as the presence of the
    // derived value: leaving a stray `theme="system"` behind would satisfy
    // the positive check on its own.
    const src = readFileSync(
      resolve(SRC, 'components/ui/ThemedToaster.jsx'), 'utf8')
    expect(src).not.toMatch(/theme="system"/)
    expect(src).toMatch(/theme=\{dark \? 'dark' : 'light'\}/)
    expect(src).toMatch(/useTheme\(\)/)
    expect(src).toMatch(/richColors/)
    expect(src).toMatch(/closeButton/)
  })
})
