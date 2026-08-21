import { useState } from 'react'

/** Run something *during render* when a value changes, rather than in an effect.
 *
 * React's own documented adjust-state-when-a-prop-changes pattern: keep the
 * previous value in state, compare on the way through, and set the derived
 * state before returning. React re-runs the component before touching the DOM,
 * so the change lands on the same commit that delivered the new value — no
 * frame is painted with the old state, and nothing joins the
 * `set-state-in-effect` backlog.
 *
 * Written out by hand in two admin components, which is one more than the
 * pattern deserves: the guard has a subtlety in it (you must set the previous
 * value on *every* change, including ones you take no action on, or the
 * comparison fires again next render) and a second copy is where that gets
 * dropped.
 *
 * `onChange` is called during render, so it must only set state on this
 * component — no fetches, no subscriptions, nothing observable outside.
 *
 * @param value     the value to watch, compared with `Object.is`
 * @param onChange  called with the new value. Deliberately not handed the
 *                  previous one: neither call site wants it, and a parameter
 *                  nobody reads is a hint that comparing against the previous
 *                  *render* is the right question here -- which for `FlowDot`
 *                  it was not, and the bug that came of assuming so is
 *                  documented there.
 */
export default function useValueChange(value, onChange) {
  const [previous, setPrevious] = useState(value)
  if (!Object.is(value, previous)) {
    setPrevious(value)
    onChange(value)
  }
}
