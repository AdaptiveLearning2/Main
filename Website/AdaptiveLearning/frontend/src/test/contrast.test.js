/**
 * Muted grey text has to clear WCAG AA on the surfaces this app actually uses.
 *
 * Contrast is arithmetic over two hex values, so it is one of the few
 * accessibility properties a source check can decide outright — unlike the
 * `AccessibleChart` rule next door, which can only check that the component
 * was used. What it cannot see is which surface a class lands on when the
 * background comes from a parent, so it judges the cases that fail on *every*
 * surface, plus same-element pairs where both are named together.
 *
 * The numbers, computed below from Tailwind 3.4's stock `gray`:
 *
 *   light  gray-400  2.54 on white .. 1.72 on gray-300   fails everywhere
 *          gray-500  4.83 on white .. 3.28 on gray-300   fails from gray-100 down
 *          gray-600  7.56 on white .. 5.13 on gray-300   passes everywhere
 *   dark   gray-500  4.16 on gray-950 .. 2.13 on gray-700  fails everywhere
 *          gray-400  7.93 on gray-950 .. 5.78 on gray-800  passes
 *
 * So the muted pair is `text-gray-600 dark:text-gray-400`.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, resolve, sep } from 'node:path'

const AA = 4.5

const GRAY = {
  50: 'f9fafb', 100: 'f3f4f6', 200: 'e5e7eb', 300: 'd1d5db', 400: '9ca3af',
  500: '6b7280', 600: '4b5563', 700: '374151', 800: '1f2937', 900: '111827',
  950: '030712',
}
const SURFACES = { white: 'ffffff', 'slate-50': 'f8fafc' }
for (const [k, v] of Object.entries(GRAY)) SURFACES[`gray-${k}`] = v

function luminance(hex) {
  const c = [0, 2, 4]
    .map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

function contrast(a, b) {
  const [la, lb] = [luminance(a), luminance(b)]
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Surfaces the app paints, per mode — read off the `bg-` classes in use. */
const LIGHT_SURFACES = ['white', 'slate-50', 'gray-50', 'gray-100', 'gray-200', 'gray-300']
const DARK_SURFACES  = ['gray-700', 'gray-800', 'gray-900', 'gray-950']

/**
 * Exempt because the surface underneath is not the one this file can infer.
 *
 * `Adaptive.jsx`'s debug readout paints `bg-gray-950` with **no** `dark:`
 * prefix, so it is dark in both modes and every rule above reverses inside it:
 * gray-400 passes at 7.93 and gray-600 would be unreadable. It is the only
 * such panel in the app — the other unprefixed dark backgrounds are tooltips
 * and overlays carrying `text-white` or no text at all.
 */
const DARK_IN_BOTH_MODES = ['pages/student/Adaptive.jsx']

function jsxFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) return jsxFiles(full)
    return name.endsWith('.jsx') && !name.endsWith('.test.jsx') ? [full] : []
  })
}

const SRC = resolve(fileURLToPath(import.meta.url), '..', '..')
const CLASS_STRING = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{"([^"]*)"\})/gs

/** Every className string in the tree, with the file it came from. */
function classStrings() {
  return jsxFiles(SRC).flatMap(file => {
    const rel = file.slice(SRC.length + 1).split(sep).join('/')
    const src = readFileSync(file, 'utf8')
    return [...src.matchAll(CLASS_STRING)]
      .map(m => ({ rel, cls: m[1] ?? m[2] ?? m[3] }))
  })
}

describe('the contrast numbers this rule rests on', () => {
  it('puts gray-400 below AA on every light surface, and gray-600 above it', () => {
    for (const s of LIGHT_SURFACES) {
      expect(contrast(GRAY[400], SURFACES[s])).toBeLessThan(AA)
      expect(contrast(GRAY[600], SURFACES[s])).toBeGreaterThanOrEqual(AA)
    }
  })

  it('puts gray-500 below AA on every dark surface, and gray-400 above it there', () => {
    for (const s of DARK_SURFACES) {
      expect(contrast(GRAY[500], SURFACES[s])).toBeLessThan(AA)
    }
    // gray-700 is the exception at 4.06, and is a hover surface rather than a
    // card — named here rather than glossed over.
    for (const s of ['gray-800', 'gray-900', 'gray-950']) {
      expect(contrast(GRAY[400], SURFACES[s])).toBeGreaterThanOrEqual(AA)
    }
  })

  it('shows gray-500 is fine on white and not on a gray-100 card', () => {
    // The reason 134 bare `text-gray-500` are left alone and the badges on
    // `bg-gray-100` were not.
    expect(contrast(GRAY[500], SURFACES.white)).toBeGreaterThanOrEqual(AA)
    expect(contrast(GRAY[500], SURFACES['gray-100'])).toBeLessThan(AA)
  })
})

/**
 * The class sets that can apply to one element *together*.
 *
 * A template literal's ternary branches are alternatives, not one set. Treating
 * the whole string as one is what let a regression ship: an element reading
 * `${on ? 'dark:text-indigo-300' : 'bg-gray-100 text-gray-400 dark:bg-gray-800'}`
 * looks like it "has a dark: text class", and the grey branch does not.
 */
function branches(cls) {
  const inner = [...cls.matchAll(/'([^']*)'|"([^"]*)"/g)].map(m => m[1] ?? m[2])
  const outside = cls.replace(/\$\{[^}]*\}/g, ' ')
  return inner.length ? [outside, ...inner] : [cls]
}

const token = (set, re) => (set.match(re) ?? [])[1]

/**
 * What actually paints, per mode.
 *
 * The fallback is the whole point: an element with no `dark:text-` renders its
 * bare colour in dark mode too. A check that only compares `dark:` against
 * `dark:` sees nothing wrong with `text-gray-600 dark:bg-gray-800`, which is
 * grey-on-charcoal at 1.94.
 */
function resolve2(set) {
  const lightFg = token(set, /(?<![:\w-])text-gray-(\d00)\b/)
  const darkFg = token(set, /\bdark:text-gray-(\d00)\b/) ?? lightFg
  const lightBg = token(set, /(?<![:\w-])bg-(?:gray-(\d00))\b/)
  const darkBg = token(set, /\bdark:bg-gray-(\d00)\b/) ?? lightBg
  return { lightFg, darkFg, lightBg, darkBg }
}

describe('muted text in the source', () => {
  it('never uses a light-mode grey that fails on every light surface', () => {
    const offenders = []
    for (const { rel, cls } of classStrings()) {
      if (DARK_IN_BOTH_MODES.includes(rel)) continue
      for (const set of branches(cls)) {
        const fg = token(set, /(?<![:\w-])text-gray-(\d00)\b/)
        // Only when it fails against *every* light surface, so a class whose
        // background comes from a parent is still judged safely.
        if (fg && LIGHT_SURFACES.every(s => contrast(GRAY[fg], SURFACES[s]) < AA)) {
          offenders.push(`${rel}: gray-${fg} fails on every light surface`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('never uses a dark-mode grey that fails on the dark surfaces it paints', () => {
    // Computed over the shade, not matched against a literal class name. The
    // first version of this test grepped for `dark:text-gray-500` specifically,
    // so `dark:text-gray-600` — worse, at 2.35 — went straight through it.
    const CARDS = ['gray-800', 'gray-900']   // 68 and 123 uses; the dark surfaces
    const offenders = []
    for (const { rel, cls } of classStrings()) {
      if (DARK_IN_BOTH_MODES.includes(rel)) continue
      for (const set of branches(cls)) {
        const fg = token(set, /\bdark:text-gray-(\d00)\b/)
        if (fg && CARDS.every(s => contrast(GRAY[fg], SURFACES[s]) < AA)) {
          offenders.push(`${rel}: dark:gray-${fg} fails on gray-800 and gray-900`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('never pairs a grey with a background it fails against, in either mode', () => {
    // Same element (or same ternary branch), so both halves are known. This is
    // the check that catches a light-mode fix which forgot its dark half: the
    // dark foreground falls back to the bare one, and `dark:bg-` moves the
    // surface out from under it.
    const offenders = []
    for (const { rel, cls } of classStrings()) {
      if (DARK_IN_BOTH_MODES.includes(rel)) continue
      for (const set of branches(cls)) {
        if (/hover|focus|active|group-/.test(set)) continue
        const { lightFg, darkFg, lightBg, darkBg } = resolve2(set)
        for (const [fg, bg, mode] of [[lightFg, lightBg, 'light'], [darkFg, darkBg, 'dark']]) {
          if (!fg || !bg) continue
          const ratio = contrast(GRAY[fg], GRAY[bg])
          if (ratio < AA) {
            offenders.push(`${rel} [${mode}]: gray-${fg} on gray-${bg} = ${ratio.toFixed(2)}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('a grey with no dark companion', () => {
  /**
   * The class the same-element check cannot see.
   *
   * These elements name no background of their own, so nothing above can
   * resolve what they sit on — but a bare `text-gray-500` with no `dark:`
   * variant renders gray-500 in dark mode too, and that is 3.67 on the
   * gray-900 card every one of these pages paints. 37 of them, pre-existing.
   *
   * The file-level test is the honest approximation: whether *this file* ever
   * paints a dark surface. Coarse, and it is what distinguishes a page whose
   * cards flip from `MainLayout`, the permanently-white marketing shell where
   * adding a dark companion would put gray-400 on white at 2.54 — a new
   * failure dressed as a fix.
   */
  it('is only allowed where the surface never goes dark', () => {
    const offenders = []
    for (const file of jsxFiles(SRC)) {
      const rel = file.slice(SRC.length + 1).split(sep).join('/')
      if (DARK_IN_BOTH_MODES.includes(rel)) continue
      const src = readFileSync(file, 'utf8')
      if (!/dark:bg-gray-(800|900|950)/.test(src)) continue   // stays light
      for (const m of src.matchAll(CLASS_STRING)) {
        const cls = m[1] ?? m[2] ?? m[3]
        for (const set of branches(cls)) {
          const fg = token(set, /(?<![:\w-])text-gray-(\d00)\b/)
          if (!fg || /dark:text-/.test(set)) continue
          // An element carrying its own light-only background stays light.
          if (/(?<![:\w-])bg-/.test(set) && !set.includes('dark:bg-')) continue
          if (contrast(GRAY[fg], SURFACES['gray-900']) < AA) {
            offenders.push(`${rel}: bare gray-${fg} with no dark: companion`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
