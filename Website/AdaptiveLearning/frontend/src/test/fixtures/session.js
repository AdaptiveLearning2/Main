/** Session and question shapes, for the practice and adaptive paths. */

export function buildSession(overrides = {}) {
  return {
    id: 'sess-1',
    started_at: '2026-08-15T10:00:00Z',
    ended_at: '2026-08-15T10:30:00Z',
    questions_answered: 6,
    correct_answers: 4,
    ...overrides,
  }
}

/** Open: no `ended_at`. Counters are still real values — an in-progress
 *  session isn't an empty one. */
export function buildOpenSession(overrides = {}) {
  return buildSession({ ended_at: null, questions_answered: 2, correct_answers: 1, ...overrides })
}

export function buildQuestion(overrides = {}) {
  return {
    id: 'q-1',
    question_text: 'What is 7 x 8?',
    options: ['54', '56', '58', '64'],
    correct_answer: '56',
    topic: 'multiplication',
    difficulty: 2,
    ...overrides,
  }
}

export function buildStats(overrides = {}) {
  return {
    total_questions: 40,
    total_correct: 28,
    current_streak: 3,
    best_streak: 7,
    retrieved: true,
    ...overrides,
  }
}

export function buildProfile(overrides = {}) {
  return {
    id: 'stu-1',
    role: 'student',
    display_name: 'Ada',
    grade_level: 5,
    difficulty_bias: 'adaptive',
    session_duration_minutes: 20,
    practice_reminders: true,
    ...overrides,
  }
}
