import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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
