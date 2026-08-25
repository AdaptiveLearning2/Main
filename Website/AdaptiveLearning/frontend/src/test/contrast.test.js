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
// A bare utility: not preceded by a variant colon, and not part of a longer word.
const bare = name => new RegExp(String.raw`(?<![:\w-])${name}\b`)

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

describe('muted text in the source', () => {
  it('never uses a light-mode grey that fails on every surface', () => {
    const offenders = classStrings()
      .filter(({ rel }) => !DARK_IN_BOTH_MODES.includes(rel))
      .filter(({ cls }) => bare('text-gray-400').test(cls) || bare('text-gray-300').test(cls))
      .map(({ rel, cls }) => `${rel}: ${cls.split(/\s+/).filter(Boolean).join(' ').slice(0, 70)}`)

    expect(offenders).toEqual([])
  })

  it('never uses a dark-mode grey that fails on every dark surface', () => {
    const offenders = classStrings()
      .filter(({ cls }) => /\bdark:text-gray-500\b/.test(cls))
      .map(({ rel, cls }) => `${rel}: ${cls.slice(0, 70)}`)

    expect(offenders).toEqual([])
  })

  it('never pairs a grey with a named background it fails against', () => {
    // Only same-element pairs: when the background comes from a parent this
    // check cannot see it, which is the honest limit of a source scan.
    const offenders = []
    for (const { rel, cls } of classStrings()) {
      for (const [, prefix, fg] of cls.matchAll(/((?:[a-z-]+:)*)text-gray-(\d00)\b/g)) {
        for (const [, bgPrefix, bg] of cls.matchAll(/((?:[a-z-]+:)*)bg-gray-(\d00)\b/g)) {
          const darkFg = prefix === 'dark:'
          const darkBg = bgPrefix === 'dark:'
          // Compare only classes that apply in the same mode, and skip
          // interaction states, which the pair may never render together.
          if (darkFg !== darkBg) continue
          if (/hover|focus|active|group/.test(prefix + bgPrefix)) continue
          const ratio = contrast(GRAY[fg], GRAY[bg])
          if (ratio < AA) {
            offenders.push(`${rel}: gray-${fg} on gray-${bg} = ${ratio.toFixed(2)}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
