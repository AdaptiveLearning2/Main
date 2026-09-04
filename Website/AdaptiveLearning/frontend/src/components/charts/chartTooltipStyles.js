/**
 * Recharts text colours by theme. Its own module rather than an export beside
 * the component, for fast refresh -- and so the test can check the colours
 * without rendering a chart.
 */

// An axis label's `fill`. Left unset, Recharts paints a label `#808080`
// regardless of theme: 3.95:1 on the white panel, under AA, and the lightest
// text on the chart at 11px -- on the one line each axis has that says what
// the axis means. `contrast.test.js` reads Tailwind classes and cannot see an
// SVG attribute, so this is chosen here rather than left to the library.
// gray-700 on white is 8.6:1; gray-300 on the gray-900 panel is 9.7:1.
export function axisLabelFill(dark) {
  return dark ? '#d1d5db' : '#374151'
}
export function tooltipStyles(dark) {
  return {
    contentStyle: {
      backgroundColor: dark ? '#111827' : '#ffffff',       // gray-900 / white
      border: `1px solid ${dark ? '#374151' : '#e5e7eb'}`, // gray-700 / gray-200
      borderRadius: 12,
      boxShadow: '0 10px 15px -3px rgba(0,0,0,0.3)',
      color: dark ? '#f9fafb' : '#111827',                 // gray-50 / gray-900
      fontSize: 12,
    },
    labelStyle: { color: dark ? '#f9fafb' : '#111827', fontWeight: 700, marginBottom: 4 },
    itemStyle: { color: dark ? '#e5e7eb' : '#374151' },    // gray-200 / gray-700
  }
}
