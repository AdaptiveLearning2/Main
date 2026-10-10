const asList = (v) => (Array.isArray(v) ? v : [])

/** Everything SessionReview derives from `/api/signals/session/{id}`; pure, so it can be memoised. */
export function buildTimeline(data) {
  const cognitive = asList(data?.cognitive)
  const face      = asList(data?.face)
  const heart     = asList(data?.heart)
  const answers   = asList(data?.answers)

  // Numeric ms x-axis, more stable than category strings.
  const cognitiveByT = new Map(
    cognitive
      .map(c => {
        const t = new Date(c.ts).getTime()
        return Number.isFinite(t) ? [t, c] : null
      })
      .filter(Boolean),
  )

  // Heart merges into cognitive rows by nearest timestamp: Recharts wants one dataset.
  const heartByT = heart
    .map(h => ({ t: new Date(h.ts).getTime(), h }))
    .filter(x => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t)

  // Union of both channels' timestamps: heart can be consented without EEG.
  const series = Array.from(new Set([...cognitiveByT.keys(), ...heartByT.map(x => x.t)]))
    .sort((a, b) => a - b)
    .map(t => {
      const c = cognitiveByT.get(t)
      return {
        t,
        focus:      typeof c?.focus      === 'number' ? c.focus      : null,
        // EEG stress is `1 - calm`, drawn as calm; a missing reading stays null, never 100%.
        calm:       typeof c?.stress     === 'number' ? 1 - c.stress : null,
      }
    })

  // Each heart reading lands on one row (the nearest), never copied across rows;
  // `dot` on the lines makes a single point visible.
  const rowIndexByT = series.map(r => r.t)
  let heartCollisions = 0
  for (const { t, h } of heartByT) {
    // Binary search for the insertion point, then compare both neighbours.
    let lo = 0, hiIdx = rowIndexByT.length
    while (lo < hiIdx) {
      const mid = (lo + hiIdx) >> 1
      if (rowIndexByT[mid] < t) lo = mid + 1
      else hiIdx = mid
    }
    const before = lo > 0 ? lo - 1 : null
    const after  = lo < series.length ? lo : null
    const dBefore = before !== null ? Math.abs(series[before].t - t) : Infinity
    const dAfter  = after  !== null ? Math.abs(series[after].t  - t) : Infinity
    const pick = dAfter < dBefore ? after : before
    // Bounded at 15s: a reading with no row that close belongs to a gap in cognitive recording.
    if (pick === null || Math.min(dBefore, dAfter) > 15_000) continue
    const row = series[pick]
    const d = Math.min(dBefore, dAfter)
    // Two readings (e.g. headband and camera at the same instant) can land on
    // one row. Closest wins, first wins a tie; the loser is counted, not merged or averaged.
    if (row.heart_rate_bpm !== undefined && row._heartDist <= d) {
      heartCollisions++
      continue
    }
    if (row.heart_rate_bpm !== undefined) heartCollisions++
    row._heartDist = d
    row.heart_rate_bpm = typeof h.heart_rate_bpm === 'number' ? h.heart_rate_bpm : null
    row.rmssd_ms = typeof h.rmssd_ms === 'number' ? h.rmssd_ms : null
  }
  if (heartCollisions) {
    console.warn(
      `[session-review] ${heartCollisions} heart reading(s) shared a plotted ` +
      'timestamp with another and are not drawn; the nearest one is shown.')
  }

  const hasHeart = series.some(r => r.heart_rate_bpm !== undefined && r.heart_rate_bpm !== null)

  // Heart sensor changes are marked, not spliced into one trace.
  const failovers = []
  for (let i = 1; i < heartByT.length; i++) {
    if (heartByT[i].h.source && heartByT[i].h.source !== heartByT[i - 1].h.source) {
      failovers.push({ t: heartByT[i].t, source: heartByT[i].h.source })
    }
  }

  // Proportion, from raw samples rather than the ribbon's 10s buckets.
  const emotionSlices = Object.entries(
    face.reduce((acc, f) => {
      if (!f.emotion) return acc          // a rejected window is not a reading
      acc[f.emotion] = (acc[f.emotion] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const stressSlices = Object.entries(
    heart.reduce((acc, h) => {
      // calibrating/unknown kept as slices, not dropped.
      const key = h.stress_category || 'unknown'
      acc[key] = (acc[key] || 0) + 1
      return acc
    }, {}),
  ).map(([name, value]) => ({ name, value }))

  const tMin = series.length ? series[0].t : 0
  const tMax = series.length ? series[series.length - 1].t : 0

  // Emotion ribbon, bucketed every ~10s.
  const ribbon = []
  let lastBucket = 0
  face.forEach(f => {
    const t = new Date(f.ts).getTime()
    if (Number.isFinite(t) && t - lastBucket > 10_000) {
      ribbon.push({ t, emotion: f.emotion })
      lastBucket = t
    }
  })

  return { cognitive, face, heart, answers, series, hasHeart, failovers,
           emotionSlices, stressSlices, tMin, tMax, ribbon }
}

const DRAWN_KEYS = ['focus', 'calm', 'heart_rate_bpm', 'rmssd_ms']

/**
 * At most `maxRows` rows for drawing, in order: per bucket the first row and each series' min
 * and max, plus the last row. The sentence and the sr-only table keep the full series.
 */
export function downsample(rows, maxRows) {
  if (!Array.isArray(rows) || rows.length <= maxRows) return rows
  // Only series with a reading take a share, so a session without heart keeps more EEG detail.
  const keys = DRAWN_KEYS.filter(k => rows.some(r => r[k] != null))
  // One slot held back for the last row, so the line reaches the end of the axis.
  const buckets = Math.max(1, Math.floor((maxRows - 1) / (1 + 2 * keys.length)))
  const size = Math.ceil(rows.length / buckets)
  const keep = new Set([rows.length - 1])
  for (let start = 0; start < rows.length; start += size) {
    const end = Math.min(start + size, rows.length)
    keep.add(start)
    for (const key of keys) {
      let lo = -1, hi = -1
      for (let i = start; i < end; i++) {
        const v = rows[i][key]
        if (v == null) continue
        if (lo < 0 || v < rows[lo][key]) lo = i
        if (hi < 0 || v > rows[hi][key]) hi = i
      }
      if (lo >= 0) keep.add(lo)
      if (hi >= 0) keep.add(hi)
    }
  }
  return [...keep].sort((a, b) => a - b).map(i => rows[i])
}
