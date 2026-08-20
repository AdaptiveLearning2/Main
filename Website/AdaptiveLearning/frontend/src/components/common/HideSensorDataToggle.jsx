/**
 * The teacher's *"Hide sensor data"* switch.
 *
 * Kept small and plain, not a card with an icon and paragraph — the control it
 * replaced looked like a consent setting, which is what made it confusing.
 * Hides **all** sensor data, not just facial, since heart rate now comes from
 * the headband as often as the camera. See `lib/viewPrefs.js` for scope.
 */

import { Eye, EyeOff } from 'lucide-react'

export default function HideSensorDataToggle({ hidden, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={hidden}
      onClick={() => onChange(!hidden)}
      // `py-2.5` keeps the tap target at the 44px minimum for touch. Padded
      // rather than given an invisible overlay hit area, since this button has
      // a visible border and growing the box is what a tap expects here.
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
