import { it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))

import { apiFetch, mockApi, overrideApi, resetApi, apiError } from '../../test/mocks/apiFetch'
import PracticeFlashcards from './PracticeFlashcards'

const SESSION = { id: 'sess-1', mode: 'flashcard' }

const CARD_ONE = {
  id: 'q1', question_text: 'What is 6 x 7?', question_topic: 'expressions', correct_answer: '42',
}
const CARD_TWO = {
  id: 'q2', question_text: 'What is 9 x 9?', question_topic: 'expressions', correct_answer: '81',
}

const draw = (onFinish = vi.fn()) => {
  const utils = render(<PracticeFlashcards session={SESSION} onFinish={onFinish} />)
  return { onFinish, ...utils }
}

beforeEach(() => {
  resetApi()
  mockApi({
    'GET /api/practice-sessions/sess-1/question': () => CARD_ONE,
    'POST /api/practice-sessions/sess-1/view': () => ({ ok: true, topic: 'expressions' }),
  })
})

it('hides the answer until flipped', async () => {
  draw()
  await screen.findByText('What is 6 x 7?')
  expect(screen.queryByText('42')).not.toBeInTheDocument()
})

it('reveals the answer on flip and records a view', async () => {
  draw()
  await screen.findByText('What is 6 x 7?')

  await userEvent.click(screen.getByRole('button', { name: /flip card/i }))

  expect(await screen.findByText('42')).toBeInTheDocument()
  await waitFor(() => {
    expect(apiFetch).toHaveBeenCalledWith('/api/practice-sessions/sess-1/view', {
      method: 'POST',
      body: { question_id: 'q1' },
    })
  })
})

it('advances to the next card after flipping', async () => {
  let calls = 0
  overrideApi('/api/practice-sessions/sess-1/question', () => (calls++ === 0 ? CARD_ONE : CARD_TWO))
  draw()
  await screen.findByText('What is 6 x 7?')
  await userEvent.click(screen.getByRole('button', { name: /flip card/i }))
  await screen.findByText('42')

  await userEvent.click(screen.getByRole('button', { name: /next/i }))

  expect(await screen.findByText('What is 9 x 9?')).toBeInTheDocument()
  expect(screen.queryByText('81')).not.toBeInTheDocument()
})

it('counts reviewed cards and ends the session on Done', async () => {
  const { onFinish } = draw()
  await screen.findByText('What is 6 x 7?')
  await userEvent.click(screen.getByRole('button', { name: /flip card/i }))
  await screen.findByText('42')
  expect(screen.getByText('1 reviewed')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /^done$/i }))

  expect(onFinish).toHaveBeenCalledWith({ questions_answered: 1, correct_answers: 0 })
})

it('reports a card that could not load, with a retry', async () => {
  overrideApi('/api/practice-sessions/sess-1/question', () => { throw apiError(500, 'down') })
  draw()
  expect(await screen.findByText(/couldn't load the next card/i)).toBeInTheDocument()
})
