import { it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import PracticeHistory from './PracticeHistory'

// A row as `GET /api/practice-sessions` returns it (`_PRACTICE_CLIENT_COLUMNS` plus `abandoned`).
const row = over => ({
  id: 'p1', mode: 'test', topics: ['ordering'], difficulty: 'easy',
  started_at: '2026-08-16T09:00:00Z', ended_at: '2026-08-16T09:20:00Z',
  questions_answered: 10, correct_answers: 8, abandoned: false, ...over,
})

it('says a session left open past the cut is not finished, not in progress', () => {
  render(<PracticeHistory sessions={[row({ id: 'left', ended_at: null, abandoned: true })]} />)
  expect(screen.getByText('Not finished')).toBeInTheDocument()
  expect(screen.queryByText('In progress')).not.toBeInTheDocument()
})

it('still says a session open now is in progress', () => {
  render(<PracticeHistory sessions={[row({ ended_at: null, abandoned: false })]} />)
  expect(screen.getByText('In progress')).toBeInTheDocument()
})

it('shows no accuracy for a test with no answers, never 0% or a card count', () => {
  render(<PracticeHistory sessions={[row({ questions_answered: 0, correct_answers: 0 })]} />)
  expect(screen.getByText('No answers')).toBeInTheDocument()
  expect(screen.queryByText('0%')).not.toBeInTheDocument()
  expect(screen.queryByText(/cards?$/)).not.toBeInTheDocument()
})

it('shows a graded test as its accuracy', () => {
  render(<PracticeHistory sessions={[row()]} />)
  expect(screen.getByText('80%')).toBeInTheDocument()
})
