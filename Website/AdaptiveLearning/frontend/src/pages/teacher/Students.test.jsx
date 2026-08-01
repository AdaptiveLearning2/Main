import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import Students from './Students'

// This page reads cognitive_signals and face_signals straight from the browser
// client. Two things about that are easy to get wrong and invisible in the UI:
// how null readings are averaged, and whether the facial-recognition switch
// actually stops the query rather than just hiding the result.

vi.mock('../../lib/supabase', () => {
  const fromCalls = []
  const results = {}
  // Every builder method the page chains returns the builder; the terminal
  // calls resolve. The builder is itself a thenable because two of the four
  // queries are awaited straight off .eq() with no limit()/maybeSingle().
  const query = (table) => {
    const result = () => results[table] ?? { data: [], error: null }
    const q = {
      select: () => q,
      eq: () => q,
      order: () => q,
      limit: () => Promise.resolve(result()),
      maybeSingle: () => Promise.resolve(result()),
      then: (res, rej) => Promise.resolve(result()).then(res, rej),
    }
    return q
  }
  return {
    supabase: {
      auth: { getUser: () => Promise.resolve({ data: { user: { id: 'teacher-1' } }, error: null }) },
      from: (table) => { fromCalls.push(table); return query(table) },
    },
    __fromCalls: fromCalls,
    __results: results,
  }
})

const { __fromCalls: fromCalls, __results: results } = await import('../../lib/supabase')

const MEMBERSHIPS = {
  data: [{
    student_id: 'stu-1',
    profiles: { id: 'stu-1', email: 'ada@example.com', username: 'ada' },
    classes: { teacher_id: 'teacher-1' },
  }],
  error: null,
}

function setTables({ cognitive, face }) {
  for (const k of Object.keys(results)) delete results[k]
  Object.assign(results, {
    class_memberships: MEMBERSHIPS,
    user_stats: { data: { total_questions: 10, total_correct: 5, current_streak: 2, best_streak: 3 }, error: null },
    cognitive_signals: { data: cognitive, error: null },
    face_signals: { data: face, error: null },
    user_math_performance: { data: [], error: null },
  })
}

// StatCard renders the value above the label, so scope by the label's parent.
function tile(label) {
  return within(screen.getByText(label).closest('div'))
}

async function expandAda() {
  await userEvent.click(await screen.findByRole('button', { name: /ada/i }))
}

beforeEach(() => {
  localStorage.clear()
  fromCalls.length = 0
  setTables({
    cognitive: [
      { focus: 0.8, stress: 0.3, engagement: 0.5 },
      { focus: null, stress: null, engagement: null },
      { focus: 0.6, stress: 0.5, engagement: 0.7 },
    ],
    face: [{ attention: 0.9, emotion: 'happy' }],
  })
})

describe('signal averages', () => {
  it('ignores null readings instead of counting them as zero', async () => {
    // Number(null) is 0 and Number.isFinite(0) is true, so converting before
    // filtering pulled the mean of [0.8, null, 0.6] down from 70% to 47%.
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Focus Score').getByText('70%')).toBeInTheDocument())
  })

  it('reports no data rather than a confident zero when every reading is null', async () => {
    setTables({
      cognitive: [{ focus: null, stress: null, engagement: null }],
      face: [{ attention: 0.9, emotion: 'happy' }],
    })
    render(<Students />)
    await expandAda()
    // "0%" here would read as a real measurement of a struggling student.
    await waitFor(() => expect(tile('Focus Score').getByText('—')).toBeInTheDocument())
  })
})

describe('facial recognition switch', () => {
  it('reads facial signals by default', async () => {
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(fromCalls).toContain('face_signals'))
    expect(tile('Face Attention').getByText('90%')).toBeInTheDocument()
  })

  it('does not query face_signals once switched off', async () => {
    // The same control the student progress report offers. Honouring it there
    // and ignoring it here would make it a promise the app does not keep.
    render(<Students />)
    await userEvent.click(await screen.findByRole('switch'))
    fromCalls.length = 0
    await expandAda()

    await waitFor(() => expect(fromCalls).toContain('cognitive_signals'))
    expect(fromCalls).not.toContain('face_signals')
  })

  it('labels the facial tiles as off rather than missing', async () => {
    render(<Students />)
    await userEvent.click(await screen.findByRole('switch'))
    await expandAda()
    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
    expect(tile('Dominant Emotion').getByText('Off')).toBeInTheDocument()
  })

  it('re-reads an open student when the switch flips', async () => {
    // The cached rows were fetched with facial data in them; leaving them on
    // screen would show exactly what the viewer just excluded.
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(tile('Face Attention').getByText('90%')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
  })

  it('remembers the choice across mounts', async () => {
    const { unmount } = render(<Students />)
    await userEvent.click(await screen.findByRole('switch'))
    unmount()

    fromCalls.length = 0
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(fromCalls).toContain('cognitive_signals'))
    expect(fromCalls).not.toContain('face_signals')
  })
})
