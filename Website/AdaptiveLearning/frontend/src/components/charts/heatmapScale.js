/**
 * Heatmap accuracy to classes. Stepped, complete class strings: Tailwind never ships an
 * interpolated class, and each pair names both themes. Every pair clears AA in both modes;
 * `contrast.test.js` computes it (white fails on emerald-500 and rose-400).
 */
export const SCALE = [
  { at: 0.85, cell: 'bg-emerald-500 dark:bg-emerald-500', text: 'text-emerald-950' },
  { at: 0.70, cell: 'bg-emerald-300 dark:bg-emerald-700', text: 'text-emerald-950 dark:text-emerald-50' },
  { at: 0.55, cell: 'bg-amber-200 dark:bg-amber-700',     text: 'text-amber-950 dark:text-amber-50' },
  { at: 0.40, cell: 'bg-orange-300 dark:bg-orange-800',   text: 'text-orange-950 dark:text-orange-50' },
  { at: 0.00, cell: 'bg-rose-400 dark:bg-rose-800',       text: 'text-rose-950 dark:text-white' },
]
