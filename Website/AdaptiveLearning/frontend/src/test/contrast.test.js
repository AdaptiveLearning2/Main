/** Muted grey text clears WCAG AA, computed rather than matched: the pair is `text-gray-600 dark:text-gray-400`. */
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

/** Unprefixed `bg-gray-950` debug readout: dark in both modes, so every rule reverses inside it. */
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
    // gray-700 (a hover surface) is the exception at 4.06.
    for (const s of ['gray-800', 'gray-900', 'gray-950']) {
      expect(contrast(GRAY[400], SURFACES[s])).toBeGreaterThanOrEqual(AA)
    }
  })

  it('shows gray-500 is fine on white and not on a gray-100 card', () => {
    expect(contrast(GRAY[500], SURFACES.white)).toBeGreaterThanOrEqual(AA)
    expect(contrast(GRAY[500], SURFACES['gray-100'])).toBeLessThan(AA)
  })
})

/** The class sets that can apply to one element together: ternary branches are alternatives. */
function branches(cls) {
  const inner = [...cls.matchAll(/'([^']*)'|"([^"]*)"/g)].map(m => m[1] ?? m[2])
  const outside = cls.replace(/\$\{[^}]*\}/g, ' ')
  return inner.length ? [outside, ...inner] : [cls]
}

const token = (set, re) => (set.match(re) ?? [])[1]

/** What paints per mode; with no `dark:text-` the bare colour paints in dark mode too. */
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
        // Only when it fails on every light surface, since the background may come from a parent.
        if (fg && LIGHT_SURFACES.every(s => contrast(GRAY[fg], SURFACES[s]) < AA)) {
          offenders.push(`${rel}: gray-${fg} fails on every light surface`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('never uses a dark-mode grey that fails on the dark surfaces it paints', () => {
    // Computed over the shade, never matched against a literal class name.
    const CARDS = ['gray-800', 'gray-900']   // the dark card surfaces
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
    // Same element or branch, so both halves are known; catches a light fix missing its dark half.
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
  // No own background, so approximated per file: does this file ever paint a dark surface?
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
