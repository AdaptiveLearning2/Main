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
  // Recorded per table so a test can assert the signal reads are bounded in
  // time, not just in rows. Without this the builder accepts .gte() silently
  // and dropping the window again would pass every assertion below.
  const gteCalls = {}
  // Every builder method the page chains returns the builder; the terminal
  // calls resolve. The builder is itself a thenable because two of the four
  // queries are awaited straight off .eq() with no limit()/maybeSingle().
  const query = (table) => {
    // An Error stored for a table rejects rather than resolving, so a test can
    // exercise the throw path as well as the { data, error } one.
    const settle = () => {
      const r = results[table] ?? { data: [], error: null }
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r)
    }
    const q = {
      select: () => q,
      eq: () => q,
      gte: (col, value) => { (gteCalls[table] ||= []).push([col, value]); return q },
      order: () => q,
      limit: () => settle(),
      maybeSingle: () => settle(),
      then: (res, rej) => settle().then(res, rej),
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
    __gteCalls: gteCalls,
  }
})

const { __fromCalls: fromCalls, __results: results, __gteCalls: gteCalls } = await import('../../lib/supabase')

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
  for (const k of Object.keys(gteCalls)) delete gteCalls[k]
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

  it('bounds both signal reads by time, not just by row count', async () => {
    // The row cap alone left these averages describing an unbounded span -- the
    // newest 200 readings, whenever they happened -- while the tiles called it a
    // window and the panel sits beside a weekly-framed toggle. A student who
    // stopped using the app months ago showed months-old averages as current.
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(gteCalls.cognitive_signals).toBeDefined())
    await waitFor(() => expect(gteCalls.face_signals).toBeDefined())

    for (const table of ['cognitive_signals', 'face_signals']) {
      const [[col, value]] = gteCalls[table]
      expect(col).toBe('ts')
      // Seven days back, to match the weekly report and the summary RPCs'
      // p_days default, so a teacher and a parent describe the same week.
      const days = (Date.now() - Date.parse(value)) / 86400000
      expect(days).toBeGreaterThan(6.9)
      expect(days).toBeLessThan(7.1)
    }
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

describe('a failed read', () => {
  it('leaves the row refetchable rather than stuck loading', async () => {
    // The loading flag is what toggleExpand checks to decide a row is already
    // handled. A read that throws without clearing it leaves the row showing a
    // spinner that collapsing and re-expanding never clears -- the same stuck
    // row the supersede path is careful to avoid.
    results.user_stats = new Error('network down')
    render(<Students />)
    await expandAda()
    await waitFor(() => expect(fromCalls.filter(t => t === 'user_stats')).toHaveLength(1))

    setTables({ cognitive: [{ focus: 0.8, stress: 0.3, engagement: 0.5 }], face: [] })
    await expandAda()   // collapse
    await expandAda()   // and retry
    await waitFor(() => expect(tile('Focus Score').getByText('80%')).toBeInTheDocument())
    expect(fromCalls.filter(t => t === 'user_stats').length).toBeGreaterThan(1)
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

  it('discards a read left in flight by a row collapsed before the switch flipped', async () => {
    // Only the expanded row's request used to be superseded. A student
    // collapsed mid-read kept its request id, so the read landed afterwards
    // and cached facial data under the new setting -- and toggleExpand skips
    // the fetch when the cache is warm, so re-expanding served it straight
    // back with the switch off.
    let resolveCog
    results.cognitive_signals = new Promise(r => { resolveCog = r })

    render(<Students />)
    await expandAda()                                   // read starts, face on
    await expandAda()                                   // collapse, still loading
    await userEvent.click(screen.getByRole('switch'))   // face off

    resolveCog({ data: [{ focus: 0.8, stress: 0.3, engagement: 0.5 }], error: null })
    await waitFor(() => expect(fromCalls).toContain('face_signals'))

    await expandAda()
    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
    // The stale read must not have satisfied the cache either -- a row that
    // never refetches shows nothing at all.
    expect(tile('Focus Score').getByText('80%')).toBeInTheDocument()
  })

  it('discards a read that lands after a newer one under the same setting', async () => {
    // Off and straight back on leaves two reads in flight that both carry
    // faceIncluded=true. Keying the staleness check on that value alone let the
    // older one land last and overwrite newer data with it.
    const deferred = () => {
      let resolve
      const promise = new Promise(r => { resolve = r })
      return { promise, resolve }
    }
    const first = deferred(), second = deferred(), third = deferred()

    results.cognitive_signals = first.promise
    render(<Students />)
    await expandAda()                                   // read 1, face on

    results.cognitive_signals = second.promise
    await userEvent.click(screen.getByRole('switch'))   // read 2, face off
    results.cognitive_signals = third.promise
    await userEvent.click(screen.getByRole('switch'))   // read 3, face on again

    third.resolve({ data: [{ focus: 0.6, stress: 0.3, engagement: 0.5 }], error: null })
    await waitFor(() => expect(tile('Focus Score').getByText('60%')).toBeInTheDocument())

    second.resolve({ data: [{ focus: 0.2, stress: 0.3, engagement: 0.5 }], error: null })
    first.resolve({ data: [{ focus: 0.1, stress: 0.3, engagement: 0.5 }], error: null })
    await waitFor(() => expect(fromCalls.filter(t => t === 'cognitive_signals')).toHaveLength(3))

    expect(tile('Focus Score').getByText('60%')).toBeInTheDocument()
    expect(screen.queryByText('10%')).not.toBeInTheDocument()
  })
})

describe('the "nothing recorded" note', () => {
  it('does not claim no sessions on the strength of facial data it never read', async () => {
    // faceSignalCount is 0 by construction with the switch off, so including
    // it in the condition unconditionally let this assert "no sessions" for a
    // student whose only recorded activity was the facial signals we were
    // asked not to look at.
    setTables({ cognitive: [], face: [{ attention: 0.9, emotion: 'happy' }] })
    results.user_stats = { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null }

    render(<Students />)
    await userEvent.click(await screen.findByRole('switch'))
    await expandAda()

    await waitFor(() => expect(tile('Face Attention').getByText('Off')).toBeInTheDocument())
    expect(screen.queryByText(/hasn't completed any sessions yet/i)).not.toBeInTheDocument()
    // What was actually checked, stated as such.
    expect(screen.getByText(/facial signals were not read/i)).toBeInTheDocument()
  })

  it('still says so when everything was read and there was nothing', async () => {
    setTables({ cognitive: [], face: [] })
    results.user_stats = { data: { total_questions: 0, total_correct: 0, current_streak: 0, best_streak: 0 }, error: null }

    render(<Students />)
    await expandAda()

    await waitFor(() =>
      expect(screen.getByText(/hasn't completed any sessions yet/i)).toBeInTheDocument())
  })
})
