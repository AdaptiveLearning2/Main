// Rows past this many enter together: at i * 0.04, a 200-row history finished fading in at 8 s.
export const STAGGER_CAP = 8

/** The entrance delay for row `i`: `base + i * step`, with `i` capped at STAGGER_CAP. */
export const stagger = (i, step, base = 0) => base + Math.min(i, STAGGER_CAP) * step
