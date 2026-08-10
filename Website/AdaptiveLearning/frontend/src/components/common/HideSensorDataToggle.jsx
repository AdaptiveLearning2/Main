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
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-xs font-bold text-gray-600 dark:text-gray-300 hover:bg-slate-50 dark:hover:bg-gray-800 transition">
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
