// One switch, after three.
//
// `teacher/Settings.jsx`, `student/Profile.jsx` and `admin/Flags.jsx` each
// defined their own, and they had already diverged in ways that were not
// stylistic: the admin copy was the only one with `role="switch"` and
// `aria-checked`, so two of the three were invisible to a screen reader as
// controls, and it named its prop `checked` while the others said `value`.
//
// **The geometry is inline-flex, not absolute.** The other two positioned the
// knob with `absolute` and needed `left-0` to do it -- load-bearing, because a
// button centres its content, so without it `left` resolved to the span's
// static position at the middle of a 44px track and the translate moved it from
// there: "on" drew at 46px (18px outside the pill) and "off" at 26px, hard
// against the right end. Both states put the knob right of centre, and neither
// looked broken enough to read as a bug. Laying the knob out in flow removes
// the trap rather than documenting it a third time.
const TONES = {
  violet:  'bg-violet-600',
  indigo:  'bg-indigo-600',
  emerald: 'bg-emerald-600',
  rose:    'bg-rose-600',
}

/**
 * @param checked   whether the switch is on
 * @param onChange  called with the *new* value
 * @param disabled  greys it out and blocks the click
 * @param tone      which colour "on" is; defaults to the app's indigo
 */
export default function Toggle({ checked, onChange, disabled = false, tone = 'indigo' }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors duration-200 disabled:opacity-40
        ${checked ? (TONES[tone] || TONES.indigo) : 'bg-gray-300 dark:bg-gray-600'}`}
    >
      <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 mt-0.5
        ${checked ? 'translate-x-5' : 'translate-x-0.5'}`} />
    </button>
  )
}
