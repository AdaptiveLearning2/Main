/**
 * The XSS sinks, shared by `eslint.config.js` (editor) and `eslint.sinks.config.js` (blocking CI).
 * They are what make the JWT in `localStorage` acceptable.
 * Never write a bare `[...name=]` selector for a property: use `eitherSpelling`
 * (`el.x` / `el['x']` are different nodes). Template-literal keys are not covered;
 * deliberate evasion can use an `eslint-disable` anyway.
 */

/** One selector matching a name written bare or quoted. `pattern`: quoted string or `/regex/`. */
const eitherSpelling = (node, path, pattern) =>
  `${node}[${path}.name=${pattern}], ${node}[${path}.value=${pattern}]`

export const sinkRules = {
  // JSX attribute only; the object and assignment routes are below.
  'react/no-danger': 'error',

  'no-restricted-syntax': [
    'error',
    {
      // Bare: a binding reference cannot be quoted.
      selector: "CallExpression[callee.name='eval']",
      message: 'eval() executes strings as code. There is no use for it here, and its presence is what would turn a stored string into script.',
    },
    {
      selector: eitherSpelling('MemberExpression', 'property', "'eval'"),
      message: 'window.eval / globalThis.eval is eval(). See the rule above it.',
    },
    {
      // Bare: a binding reference cannot be quoted.
      selector: "NewExpression[callee.name='Function']",
      message: 'new Function() compiles a string into a function, which is eval by another name.',
    },
    {
      // Bare: a binding reference cannot be quoted.
      selector: "CallExpression[callee.name='Function']",
      message: 'Function() compiles a string into a function, which is eval by another name.',
    },
    {
      selector: eitherSpelling('MemberExpression', 'property', "'Function'"),
      message: 'window.Function is Function(), which compiles a string into a function.',
    },
    {
      selector: eitherSpelling('AssignmentExpression', 'left.property', '/^(inner|outer)HTML$/'),
      message: 'Assigning innerHTML/outerHTML parses the string as markup. Set textContent, or render it through React.',
    },
    {
      // `Object.assign(el, {innerHTML: s})`. Also flags a destructuring read
      // (known false positive, no hits in `src`).
      selector: eitherSpelling('Property', 'key', '/^(inner|outer)HTML$/'),
      message: 'An innerHTML/outerHTML key parses its value as markup once the object reaches an element.',
    },
    {
      selector: eitherSpelling('CallExpression', 'callee.property', "'insertAdjacentHTML'"),
      message: 'insertAdjacentHTML parses its argument as markup, exactly as innerHTML does.',
    },
    {
      // On the method name, not `document`, so an alias is caught too.
      selector: eitherSpelling('CallExpression', 'callee.property', '/^write(ln)?$/'),
      message: 'write/writeln parse their argument as markup.',
    },
    {
      // `<div {...{dangerouslySetInnerHTML: x}} />`, which `react/no-danger` misses.
      selector: eitherSpelling('Property', 'key', "'dangerouslySetInnerHTML'"),
      message: 'dangerouslySetInnerHTML injects unparsed markup. Nothing in this app needs it; see eslint.sinks.js.',
    },
    {
      // `props.dangerouslySetInnerHTML = x`, then spread.
      selector: eitherSpelling('AssignmentExpression', 'left.property', "'dangerouslySetInnerHTML'"),
      message: 'dangerouslySetInnerHTML injects unparsed markup, assigned onto a props object as much as written in JSX.',
    },
  ],
}
