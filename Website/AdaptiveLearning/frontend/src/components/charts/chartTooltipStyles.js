/**
 * Recharts tooltip styles by theme. Its own module rather than an export
 * beside the component, for fast refresh -- and so the test can check the
 * colours without rendering a chart.
 */
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
