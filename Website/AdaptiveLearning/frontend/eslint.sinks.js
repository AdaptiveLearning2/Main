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
 * **A selector covers one node type, and reading alike is not being alike.**
 * `Property[key.name=x]` and `AssignmentExpression[left.property.name=x]` are
 * different nodes for what a reader calls "setting x", so a sink reachable
 * both ways needs both -- and a comment claiming one selector covers both is
 * worse than the gap, because it stops anyone checking. When adding a sink,
 * plant every spelling of it in a scratch file, lint that file, and read the
 * report rather than the rule.
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
      // Matched on the method name, not on `document`: `const d = document;
      // d.write(s)` is the same sink, and pinning the object let an alias
      // through. `writeln` is the same API one word along, and the message is
      // true of it verbatim. Nothing in `src/` calls `.write(`/`.writeln(` on
      // anything, so the wider match costs nothing; if something legitimate
      // ever does, narrowing it then is a decision someone makes on purpose.
      selector: "CallExpression[callee.property.name=/^write(ln)?$/]",
      message: 'write/writeln parse their argument as markup.',
    },
    // `dangerouslySetInnerHTML` reached three ways, and `react/no-danger` sees
    // only the first. Each needs its own selector -- a `Property` and an
    // `AssignmentExpression` are different nodes, so one does not imply the
    // other however alike they read.
    {
      // `<div {...{dangerouslySetInnerHTML: x}} />`, and any object literal
      // carrying the key on its way to becoming props.
      selector: "Property[key.name='dangerouslySetInnerHTML']",
      message: 'dangerouslySetInnerHTML injects unparsed markup. Nothing in this app needs it; see eslint.sinks.js.',
    },
    {
      // `props.dangerouslySetInnerHTML = x`, then spread. The one route that
      // is neither a JSX attribute nor an object literal, and the shape is
      // the one the innerHTML rule above already uses.
      selector: "AssignmentExpression[left.type='MemberExpression'][left.property.name='dangerouslySetInnerHTML']",
      message: 'dangerouslySetInnerHTML injects unparsed markup, assigned onto a props object as much as written in JSX.',
    },
    {
      // `Object.assign(el, {innerHTML: s})` and any other object literal that
      // becomes an element's properties. The assignment rule above is the
      // statement form of the same sink.
      selector: "Property[key.name=/^(inner|outer)HTML$/]",
      message: 'An innerHTML/outerHTML key parses its value as markup once the object reaches an element.',
    },
  ],
}
