/**
 * The motion guard, added to `eslint.config.js` (editor) and `eslint.sinks.config.js` (blocking CI).
 * App.jsx loads `domAnimation` into <LazyMotion strict>: `m` elements only, with no drag, pan or layout.
 * Not seen: a prop passed through a spread, or an `m` element under another name.
 */

/** Every prop served only by the drag, pan or layout feature; `motionImports.test.js` derives it from motion-dom's types. */
export const UNSERVED_PROPS = [
  'drag', 'whileDrag', 'dragDirectionLock', 'dragPropagation', 'dragConstraints', 'dragElastic',
  'dragMomentum', 'dragTransition', 'dragControls', 'dragSnapToOrigin', 'dragListener',
  'onMeasureDragConstraints', '_dragX', '_dragY',
  'onDragStart', 'onDragEnd', 'onDrag', 'onDirectionLock', 'onDragTransitionEnd',
  'onPan', 'onPanStart', 'onPanSessionStart', 'onPanEnd',
  'layout', 'layoutId', 'layoutDependency', 'layoutScroll', 'layoutRoot', 'layoutAnchor', 'layoutCrossfade',
  'data-framer-portal-id', 'onLayoutAnimationStart', 'onLayoutAnimationComplete', 'onBeforeLayoutMeasure', 'onLayoutMeasure',
]

// Modules whose every export is a full-feature `motion` component.
const CLIENT_MODULES = ['framer-motion/client', 'motion/react-client']
// Modules exporting `motion` beside `m`.
const MAIN_MODULES = ['framer-motion', 'motion/react']

const USE_M = 'Use `m`; <LazyMotion> in App.jsx supplies its features.'

const IMPORT_PATHS = [
  ...MAIN_MODULES.map(name => ({ name, importNames: ['motion'], message: USE_M })),
  ...CLIENT_MODULES.map(name => ({ name, message: `Every export of ${name} is a full \`motion\` component. ${USE_M}` })),
]

// Quoted values, not a regex: esquery's regex literal cannot contain `/`.
const dynamicImportOf = modules => modules.map(m => `ImportExpression[source.value='${m}']`).join(', ')

const SYNTAX = [
  {
    selector: dynamicImportOf([...MAIN_MODULES, ...CLIENT_MODULES]),
    message: 'A dynamic import of this module is a namespace import, which can reach `motion`. Import `m` from it statically.',
  },
  {
    selector: `JSXOpeningElement[name.object.name=/^(m|motion)$/] > JSXAttribute[name.name=/^(${UNSERVED_PROPS.join('|')})$/]`,
    message: 'App.jsx loads `domAnimation`, which has no drag, pan or layout feature, so this prop does nothing. Load `domMax` there first.',
  },
]

/** `rules` plus the motion guard. Flat config replaces a rule's options, so `no-restricted-syntax` is appended to. */
export const withMotionRules = rules => ({
  ...rules,
  'no-restricted-imports': ['error', { paths: IMPORT_PATHS }],
  'no-restricted-syntax': [...(rules['no-restricted-syntax'] ?? ['error']), ...SYNTAX],
})
