import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import CCSSBadge from './CCSSBadge'
import { normalizeQuestion } from '../../lib/practiceQuestion'

describe('CCSSBadge', () => {
  it('names the standard', () => {
    render(<CCSSBadge standard="8.G.5" />)
    expect(screen.getByText('CCSS 8.G.5')).toBeInTheDocument()
  })

  it.each([
    ['no standard', undefined],
    ['a null standard', null],
    ['an empty standard', '   '],
    ['a non-string', 42],
  ])('renders nothing for %s', (_label, standard) => {
    const { container } = render(<CCSSBadge standard={standard} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('normalizeQuestion', () => {
  const raw = { question_text: 'q', answer_options: ['a'], correct_answer: 'a' }

  it('carries the standard through to test mode', () => {
    expect(normalizeQuestion({ ...raw, ccss_standard: '6.EE.7' }).ccssStandard).toBe('6.EE.7')
  })

  it('is null, not undefined, for a row predating the column', () => {
    expect(normalizeQuestion(raw).ccssStandard).toBeNull()
  })
})

describe('every surface that presents a question', () => {
  // The same exhaustiveness check QuestionFigure.test.jsx runs, for the same
  // reason: an enrichment field shipped wired into some surfaces and not
  // others, and nothing said which.
  const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..')
  const REFERENCES_ONLY = [resolve(root, 'pages', 'teacher', 'Dashboard.jsx')]

  const walk = (dir) => readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full)
      : full.endsWith('.jsx') && !full.includes('.test.') ? [full] : []
  })

  it('shows its standard too', () => {
    const presenting = walk(root)
      .filter(f => !REFERENCES_ONLY.includes(f))
      .filter(f => /\{(q|data|question)\.(question_text|text)\}/.test(
        readFileSync(f, 'utf8')))
    expect(presenting.length).toBeGreaterThan(0)
    // `<CCSSBadge`, not the bare name -- a dangling import satisfies that.
    const missing = presenting.filter(
      f => !readFileSync(f, 'utf8').includes('<CCSSBadge'))
    expect(missing).toEqual([])
  })
})
