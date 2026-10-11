// Legend text wears a text colour, never the series colour: identity is the icon's job.
export const legendText = value => <span className="text-gray-700 dark:text-gray-300">{value}</span>

// A line chart's legend icon is the line itself, dash included.
export const LINE_LEGEND = { iconType: 'plainline', formatter: legendText }
