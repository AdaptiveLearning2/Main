import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import LoadError from './LoadError'

/**
 * The sentence this component picks is the one someone acts on. It used to
 * pick the same one for everything -- "make sure the backend is running" --
 * which sent a teacher to check a server that had answered correctly and
 * refused. What is under test here is that a refusal, a lapsed session and an
 * unreachable backend stay three different statements.
 */
describe('LoadError', () => {
  const err = (status) => Object.assign(new Error('nope'), { status })

  it('blames the backend only when the request never got an answer', () => {
    // No `status` is the shape of a dropped connection or an aborted fetch:
    // the one case where "is the backend running" is the right question.
    render(<LoadError what="your classes" error={new Error('network down')} />)
    expect(screen.getByRole('status')).toHaveTextContent(
      "Couldn't load your classes. Make sure the backend is running.")
  })

  it('says the same with no error at all, so an unwired caller is unchanged', () => {
    // Twelve call sites pass nothing. Their wording must not move.
    render(<LoadError what="your classes" />)
    expect(screen.getByRole('status')).toHaveTextContent(
      "Couldn't load your classes. Make sure the backend is running.")
  })

  it('calls a 403 what it is, and does not mention the backend', () => {
    render(<LoadError what="this student's questions" error={err(403)} />)
    const box = screen.getByRole('status')
    expect(box).toHaveTextContent("You don't have access to this student's questions.")
    // The load-bearing half. The old copy was not merely vague, it named the
    // wrong layer, and naming a layer is what makes someone go and look at it.
    expect(box).not.toHaveTextContent(/backend/i)
  })

  it('withholds Try again on a 403, because retrying cannot work', () => {
    const onRetry = vi.fn()
    render(<LoadError what="this class" error={err(403)} onRetry={onRetry} />)
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('keeps Try again on a 401, which a restored session can fix', () => {
    // Deliberately not folded in with 403. Both are "you may not have this",
    // but only one of them can come good without the user doing anything
    // else, so collapsing them would either hide a usable button or offer a
    // dead one.
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
 * Exhaustiveness, like the backend's `_MODE_AWARE` and close-site tests.
 *
 * `error` is optional, so a call site that never passes one is silently back
 * to blaming the backend for a refusal -- and unlike the other guards in this
 * repo, "every call site must pass one" is the wrong rule here. Most of these
 * pages read the caller's own data and cannot 403 on a relationship, and one
 * of them has no error object to pass at all. So this is a classification
 * list, not a requirement: a new file rendering `<LoadError` fails until
 * someone says which kind it is, which is the decision that would otherwise
 * be skipped.
 */
describe('every LoadError call site is classified', () => {
  const SRC = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const rel = f => relative(SRC, f).replaceAll('\\', '/')

  const walk = (dir) => readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full)
      : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
  })

  // Reads data belonging to somebody else, through a relationship check that
  // answers 403 when it does not hold. The value is the endpoint, so the
  // claim can be re-checked against the backend rather than taken on trust.
  const MUST_PASS_ERROR = {
    'pages/teacher/Questions.jsx':
      'GET /api/students/{id}/questions -- _verify_can_view_student',
    'pages/teacher/Sessions.jsx':
      'GET /api/classes/{id}/students -- _verify_class_owner',
  }

  // Exempt, and the reason is the point: two different reasons hide here, and
  // only the second is about the data.
  const NO_REFUSAL_TO_REPORT = {
    // No error object exists to pass. `Panel`'s `failed` is the payload's
    // `retrieved` flag, because these aggregates answer 200 with a default
    // payload when they fail server-side, and the pages fold a rejected fetch
    // into that same flag on the way in. The status is discarded at that
    // conversion, so wiring this means seven callers keeping the error beside
    // the flag -- a real change, not a prop.
    'components/analytics/Panel.jsx': 'failed is a retrieved flag, not an error',

    // The rest read the caller's own data, so no relationship check stands
    // between them and it and 403 is not reachable. Passing the error would
    // be harmless and would say nothing. Note the endpoint, not the page: two
    // of these files DO call a relationship-checked endpoint elsewhere --
    // Classes.jsx PUTs /api/classes/{id}, Settings.jsx reads a single child --
    // and both send those failures to a toast rather than here.
    'pages/parent/Settings.jsx':            'GET /api/parent/children -- own children',
    'pages/student/Achievements.jsx':       'GET /api/stats/me -- own',
    'pages/student/History.jsx':            'GET /api/sessions -- own',
    'pages/student/JoinClass.jsx':          'GET /api/classes -- own',
    'pages/student/PracticeFlashcards.jsx': 'own practice session',
    'pages/student/PracticeTest.jsx':       'own practice session',
    'pages/teacher/Analytics.jsx':          'the question bank, which is public-read',
    'pages/teacher/Classes.jsx':            'GET /api/classes -- own classes',
    // Same read; the relationship-checked live roster goes to its own banner.
    'pages/teacher/Live.jsx':               'GET /api/classes -- own classes',
    'components/practice/PracticeSetup.jsx': 'own profile and the topic list',
  }

  // `<LoadError`, not the bare name: a dangling import satisfies the name,
  // which is exactly what deleting the element leaves behind. That mistake
  // has already been made once here, in QuestionFigure.test.jsx.
  const callSites = walk(SRC)
    .filter(f => readFileSync(f, 'utf8').includes('<LoadError'))
    .map(rel)

  it('finds the call sites at all', () => {
    // Without this the checks below pass vacuously against a walk that
    // matched nothing -- a renamed directory would read as a clean sweep.
    expect(callSites.length).toBeGreaterThan(5)
  })

  it('leaves no call site unclassified', () => {
    const unclassified = callSites.filter(
      f => !(f in MUST_PASS_ERROR) && !(f in NO_REFUSAL_TO_REPORT))
    expect(unclassified).toEqual([])
  })

  it('has no stale entry naming a file that no longer renders one', () => {
    // A list that outlives its subject is how an exemption granted for one
    // reason gets inherited by whatever takes the file's place.
    const declared = [...Object.keys(MUST_PASS_ERROR),
                      ...Object.keys(NO_REFUSAL_TO_REPORT)]
    expect(declared.filter(f => !callSites.includes(f))).toEqual([])
  })

  /**
   * The `<LoadError ... />` elements in a file, as source text.
   *
   * Scanning to the first `>` at brace depth 0, rather than a regex, because
   * `onRetry={() => retry()}` puts a `>` inside the props and `[^>]*` would
   * cut the element in half there.
   */
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
    // On the element, not anywhere in the file -- which is the substitution
    // the comment above `callSites` warns about, made one test lower. A file
    // with any other `error={` in it (a catch block, another component's
    // prop) satisfied a file-wide search, so dropping the prop from the
    // element itself kept this green and put "make sure the backend is
    // running" back on a 403.
    const notPassing = Object.keys(MUST_PASS_ERROR).filter((f) => {
      const elements = loadErrorElements(readFileSync(join(SRC, f), 'utf8'))
      return elements.length === 0
        || !elements.every(el => el.includes('error={'))
    })
    expect(notPassing).toEqual([])
  })

  it('reads the element rather than the file', () => {
    // The extractor is the load-bearing part of the check above, so it is
    // pinned directly: a props blob containing a `>` inside an arrow function
    // must not truncate, and a stray `error={` elsewhere in the file must not
    // count.
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
