import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import LoadError from './LoadError'

/** A refusal, a lapsed session and an unreachable backend are three different sentences. */
describe('LoadError', () => {
  const err = (status) => Object.assign(new Error('nope'), { status })

  it('blames the backend only when the request never got an answer', () => {
    // No `status`: a dropped connection or aborted fetch.
    render(<LoadError what="your classes" error={new Error('network down')} />)
    expect(screen.getByRole('status')).toHaveTextContent(
      "Couldn't load your classes. Make sure the backend is running.")
  })

  it('says the same with no error at all, so an unwired caller is unchanged', () => {
    render(<LoadError what="your classes" />)
    expect(screen.getByRole('status')).toHaveTextContent(
      "Couldn't load your classes. Make sure the backend is running.")
  })

  it('calls a 403 what it is, and does not mention the backend', () => {
    render(<LoadError what="this student's questions" error={err(403)} />)
    const box = screen.getByRole('status')
    expect(box).toHaveTextContent("You don't have access to this student's questions.")
    // Naming a layer sends someone to inspect it.
    expect(box).not.toHaveTextContent(/backend/i)
  })

  it('withholds Try again on a 403, because retrying cannot work', () => {
    const onRetry = vi.fn()
    render(<LoadError what="this class" error={err(403)} onRetry={onRetry} />)
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('keeps Try again on a 401, which a restored session can fix', () => {
    render(<LoadError what="your classes" error={err(401)} onRetry={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent(/session has expired/i)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('keeps Try again for a status it has no special sentence for', () => {
    render(<LoadError what="your classes" error={err(500)} onRetry={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent(/backend is running/)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('still shows no button when the caller offered no retry', () => {
    render(<LoadError what="your classes" error={err(500)} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

/**
 * Every `<LoadError` call site must be classified: whether a 403 is reachable there.
 * A classification list, not a requirement to pass `error` everywhere.
 */
describe('every LoadError call site is classified', () => {
  const SRC = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const rel = f => relative(SRC, f).replaceAll('\\', '/')

  const walk = (dir) => readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full)
      : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
  })

  // Reads someone else's data behind a check that can 403; the value names the endpoint.
  const MUST_PASS_ERROR = {
    'pages/teacher/Questions.jsx':
      'GET /api/students/{id}/questions -- _verify_can_view_student',
    'pages/teacher/Sessions.jsx':
      'GET /api/classes/{id}/students -- _verify_class_owner',
    // Two sites; the stricter class applies to the file.
    'pages/teacher/Live.jsx':
      'GET /api/teacher/classes/{id}/live -- _verify_class_owner',
    // Admin-gated; `retrieved: false` deliberately does not come through LoadError.
    'pages/admin/SecurityEvents.jsx':
      'GET /api/admin/security-events -- _require_admin',
  }

  const NO_REFUSAL_TO_REPORT = {
    // No error object exists: `failed` is the payload's `retrieved` flag.
    'components/analytics/Panel.jsx': 'failed is a retrieved flag, not an error',

    // Own data, so 403 is unreachable on the endpoint that feeds LoadError.
    'pages/parent/Settings.jsx':            'GET /api/parent/children -- own children',
    'pages/student/Achievements.jsx':       'GET /api/stats/me -- own',
    'pages/student/History.jsx':            'GET /api/sessions -- own',
    'pages/student/JoinClass.jsx':          'GET /api/classes -- own',
    'pages/student/PracticeFlashcards.jsx': 'own practice session',
    'pages/student/PracticeTest.jsx':       'own practice session',
    'pages/teacher/Analytics.jsx':          'the question bank, which is public-read',
    'pages/teacher/Classes.jsx':            'GET /api/classes -- own classes',
    'components/practice/PracticeSetup.jsx': 'own profile and the topic list',
  }

  // `<LoadError`, not the bare name: a dangling import satisfies the name.
  const callSites = walk(SRC)
    .filter(f => readFileSync(f, 'utf8').includes('<LoadError'))
    .map(rel)

  it('finds the call sites at all', () => {
    // Otherwise the checks below pass vacuously.
    expect(callSites.length).toBeGreaterThan(5)
  })

  it('leaves no call site unclassified', () => {
    const unclassified = callSites.filter(
      f => !(f in MUST_PASS_ERROR) && !(f in NO_REFUSAL_TO_REPORT))
    expect(unclassified).toEqual([])
  })

  it('has no stale entry naming a file that no longer renders one', () => {
    const declared = [...Object.keys(MUST_PASS_ERROR),
                      ...Object.keys(NO_REFUSAL_TO_REPORT)]
    expect(declared.filter(f => !callSites.includes(f))).toEqual([])
  })

  /** The `<LoadError ... />` elements as source text; brace-depth scan, since props can hold `>`. */
  const loadErrorElements = (src) => {
    const found = []
    let start = src.indexOf('<LoadError')
    while (start !== -1) {
      let depth = 0
      let i = start + '<LoadError'.length
      for (; i < src.length; i++) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        else if (src[i] === '>' && depth === 0) break
      }
      found.push(src.slice(start, i))
      start = src.indexOf('<LoadError', i)
    }
    return found
  }

  it('passes the error everywhere a refusal is reachable', () => {
    // On the element, not anywhere in the file: any other `error={` would satisfy a file-wide search.
    const notPassing = Object.keys(MUST_PASS_ERROR).filter((f) => {
      const elements = loadErrorElements(readFileSync(join(SRC, f), 'utf8'))
      return elements.length === 0
        || !elements.every(el => el.includes('error={'))
    })
    expect(notPassing).toEqual([])
  })

  it('reads the element rather than the file', () => {
    // Pins the extractor: `>` in an arrow must not truncate, a stray `error={` must not count.
    const src = [
      'const a = <LoadError onRetry={() => go()} error={err} />',
      'function b() { try {} catch (error) { report({ error: e }) } }',
      'const c = <LoadError onRetry={() => go()} />',
    ].join('\n')
    const elements = loadErrorElements(src)
    expect(elements).toHaveLength(2)
    expect(elements[0]).toContain('error={')
    expect(elements[1]).not.toContain('error={')
  })
})
