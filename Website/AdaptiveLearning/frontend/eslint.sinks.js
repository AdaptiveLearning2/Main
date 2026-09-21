/**
 * The XSS sinks, in one place, because two configs apply them.
 *
 * The Supabase JWT lives in `localStorage` (the SDK default), readable by any
 * same-origin script. That is acceptable *because* this app has no way to run
 * injected markup: there is no `dangerouslySetInnerHTML`, no `eval`, no
 * `innerHTML` assignment, and no markdown or LaTeX renderer anywhere in
 * `src/`. Those rules keep that true rather than describing a snapshot of it.
 *
 * Verified zero hits when added, so every one of these is an `error` with
 * nothing pre-existing behind it -- unlike the fourteen in the lint backlog,
 * which is why these can gate CI and `npm run lint` still cannot.
 *
 * **Exported rather than written twice.** `eslint.config.js` carries them so
 * an editor flags a sink as it is typed, and `eslint.sinks.config.js` carries
 * them alone so CI can fail on them without also failing on the backlog. Two
 * literals would drift, and the copy that drifts is the one nobody runs
 * locally -- the same argument `AccessibleChart`'s single `columns` spec
 * makes.
 */
export const sinkRules = {
  // JSX's own sink, and the only one with a React-specific name.
  'react/no-danger': 'error',

  // Everything else is plain JS, matched on shape. `no-restricted-globals`
  // would miss `window.eval` and a member assignment both.
  'no-restricted-syntax': [
    'error',
    {
      selector: "CallExpression[callee.name='eval']",
      message: 'eval() executes strings as code. There is no use for it here, and its presence is what would turn a stored string into script.',
    },
    {
      selector: "MemberExpression[property.name='eval']",
      message: 'window.eval / globalThis.eval is eval(). See the rule above it.',
    },
    {
      selector: "NewExpression[callee.name='Function']",
      message: 'new Function() compiles a string into a function, which is eval by another name.',
    },
    {
      selector: "CallExpression[callee.name='Function']",
      message: 'Function() compiles a string into a function, which is eval by another name.',
    },
    {
      selector: "AssignmentExpression[left.type='MemberExpression'][left.property.name=/^(inner|outer)HTML$/]",
      message: 'Assigning innerHTML/outerHTML parses the string as markup. Set textContent, or render it through React.',
    },
    {
      selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
      message: 'insertAdjacentHTML parses its argument as markup, exactly as innerHTML does.',
    },
    {
      selector: "CallExpression[callee.object.name='document'][callee.property.name='write']",
      message: 'document.write parses its argument as markup.',
    },
    {
      // Catches the object form -- `{...{dangerouslySetInnerHTML: x}}` and
      // `props.dangerouslySetInnerHTML = x` -- which `react/no-danger` does
      // not see, since that rule reads JSX attributes.
      selector: "Property[key.name='dangerouslySetInnerHTML']",
      message: 'dangerouslySetInnerHTML injects unparsed markup. Nothing in this app needs it; see eslint.sinks.js.',
    },
  ],
}
