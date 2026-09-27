/** Which stored option is right: `correct_answer` is text, matched by value, never an index field. */
import { it, expect } from 'vitest'
import { correctIndex, optionList } from './answerKey'

it.each([
  [{ options: ['54', '56', '58'], correct_answer: '56' }, 1],
  [{ options: ['Apple', 'banana'], correct_answer: ' BANANA ' }, 1],           // trimmed, any case
  [{ options: ['3', '3', '4'], correct_answer: '3' }, 0],                       // first match only
  [{ options: [['2', '5'], ['2', '7']], correct_answer: '["2", "5"]' }, 0],     // a mode's list answer
  [{ options: ['a', 'b', 'c'], correct_answer: '2' }, 2],                       // a stored index
  [{ options: ['a', 'b'], correct_answer: '9' }, -1],                           // out of range
  [{ options: ['a', 'b'], correct_answer: null }, -1],
])('finds the right option in %j', (q, expected) => {
  expect(correctIndex(q, optionList(q))).toBe(expected)
})

it('reads options stored as an object, and nothing else', () => {
  expect(optionList({ options: { a: '1', b: '2' } })).toEqual(['1', '2'])
  expect(optionList({ options: 'not a list' })).toEqual([])
  expect(optionList(null)).toEqual([])
})
