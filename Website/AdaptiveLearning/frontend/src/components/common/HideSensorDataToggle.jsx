/**
 * The teacher's *"Hide sensor data"* switch.
 *
 * Deliberately small and plain — a labelled switch in a row of view options,
 * not a standalone card with an icon and an explanatory paragraph. The control
 * it replaces looked like a consent setting, and that is what made it
 * confusing; if this looks like one, it will be read as one.
 *
 * Hides **all** sensor-derived data, not just facial. Heart rate now comes from
 * the headband as often as the camera, so a "facial" filter would leave heart
 * rate and HRV on screen and satisfy nobody.
 *
 * See `lib/viewPrefs.js` for why this is client-side only and where it applies.
 */

import { Eye, EyeOff } from 'lucide-react'

export default function HideSensorDataToggle({ hidden, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={hidden}
      onClick={() => onChange(!hidden)}
      // `py-2.5` rather than `py-1.5`: at text-xs the control drew about 28px
      // tall, under the 44px minimum once a finger rather than a cursor is
      // aiming at it. The second of the two switches the accessibility pass
      // named; `ConsentChannels`' was fixed and this one was missed.
      //
      // Padded rather than given an overlay hit area like the drawer's, because
      // this button has visible text and a border: growing the box is what a
      // reader expects here, where an invisible margin around a bordered
      // control would let a tap land outside something that looks tappable.
      className="inline-flex items-center gap-2 px-3 py-2.5 min-h-[44px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-xs font-bold text-gray-600 dark:text-gray-300 hover:bg-slate-50 dark:hover:bg-gray-800 transition">
      {hidden ? <EyeOff size={14} /> : <Eye size={14} />}
      Hide sensor data
      <span className={`ml-1 w-8 h-4 rounded-full relative transition ${
        hidden ? 'bg-indigo-600' : 'bg-gray-300 dark:bg-gray-600'
      }`}>
        <span className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${
          hidden ? 'left-4' : 'left-0.5'
        }`} />
      </span>
    </button>
  )
}
