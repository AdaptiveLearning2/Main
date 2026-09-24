/**
 * The teacher's "Hide sensor data" switch; deliberately plain so it never reads
 * as a consent setting. Hides all sensor data. See `lib/viewPrefs.js`.
 */

import { Eye, EyeOff } from 'lucide-react'

export default function HideSensorDataToggle({ hidden, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={hidden}
      onClick={() => onChange(!hidden)}
      // 44px minimum touch target.
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
