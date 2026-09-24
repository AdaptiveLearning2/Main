import { useState } from 'react'

/**
 * Run something during render when a value changes (React's adjust-state-on-prop-change pattern).
 * Compares against the previous *render*, not the last value acted on (see `FlowDot`).
 * `onChange` runs during render: it may only set this component's state.
 * @param value     compared with `Object.is`
 */
export default function useValueChange(value, onChange) {
  const [previous, setPrevious] = useState(value)
  if (!Object.is(value, previous)) {
    setPrevious(value)
    onChange(value)
  }
}
