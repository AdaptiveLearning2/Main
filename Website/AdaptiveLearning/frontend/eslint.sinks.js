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
 * **A name has two spellings and they are different nodes.** `el.innerHTML`
 * and `{innerHTML: s}` put the name in `.name`, an Identifier; `el['innerHTML']`
 * and `{'innerHTML': s}` put it in `.value`, a string Literal. A selector
 * written for one matches none of the other, so `<div {...{'dangerouslySet
 * InnerHTML': {__html: userText}}} />` went through a green gate on one pair
 * of quotes. `eitherSpelling` below generates both, because this was written
 * by hand twice and both times half the forms were missed -- the second time
 * with a comment claiming otherwise, which is worse than the gap for stopping
 * anyone checking.
 *
 * **So the rule is: never write a bare `[...name=]` selector for a property.**
 * Go through `eitherSpelling`, and when adding a sink plant every spelling of
 * it in a scratch file, lint that file, and read the report rather than the
 * rule.
 *
 * `callee.name` is the one exemption, and the three below are all of it: a
 * *binding* reference cannot be quoted — `eval(s)` and `Function(s)` name
 * identifiers in scope, where `['eval']` would be a property access on
 * something, which the `MemberExpression` rules cover. Each of those three
 * says so at its own selector, because a reader checking them against this
 * paragraph should not have to infer which case they are.
 *
 * Not covered, and deliberately: a **template-literal** computed key
 * (`el[`innerHTML`] = s`) is a third spelling this gate does not catch. Nobody
 * writes a backtick computed member access by accident, and anyone writing one
 * on purpose can defeat the gate with a disable comment instead — the same
 * reason the blanket-`eslint-disable` hole is left alone. So this covers the
 * two spellings ordinary style produces, not every spelling the grammar allows.
 * Sinks that are not spellings of a name here at all — `setHTMLUnsafe`,
 * `iframe.srcdoc`, `createContextualFragment` — are uncovered too, with no
 * anchor in `src` to hang them on: there is no `<iframe>`, no `srcdoc` and no
 * markup string anywhere, and `QuestionFigure` renders a spec into React SVG
 * elements rather than markup. Add one when something plausibly wants it.
 *
 * **Exported rather than written twice.** `eslint.config.js` carries them so
 * an editor flags a sink as it is typed, and `eslint.sinks.config.js` carries
 * them alone so CI can fail on them without also failing on the backlog. Two
 * literals would drift, and the copy that drifts is the one nobody runs
 * locally -- the same argument `AccessibleChart`'s single `columns` spec
 * makes.
 */

/**
 * One selector matching a name written bare or quoted.
 *
 * esquery reads a comma as a union, so this stays one rule with one message
 * either way. `pattern` is an esquery attribute value -- a quoted string or a
 * `/regex/`.
 */
const eitherSpelling = (node, path, pattern) =>
  `${node}[${path}.name=${pattern}], ${node}[${path}.value=${pattern}]`

export const sinkRules = {
  // JSX's own sink, and the only one with a React-specific name. It reads JSX
  // attributes, so it sees `<div dangerouslySetInnerHTML={…} />` and none of
  // the object or assignment routes below.
  'react/no-danger': 'error',

  // Everything else is plain JS, matched on shape. `no-restricted-globals`
  // would miss `window.eval` and a member assignment both.
  'no-restricted-syntax': [
    'error',
    {
      // A bare call. `eval` as an identifier has only this spelling -- you
      // cannot quote a reference to a binding.
      selector: "CallExpression[callee.name='eval']",
      message: 'eval() executes strings as code. There is no use for it here, and its presence is what would turn a stored string into script.',
    },
    {
      // `window.eval`, `globalThis['eval']`, and anything else reaching it as
      // a property.
      selector: eitherSpelling('MemberExpression', 'property', "'eval'"),
      message: 'window.eval / globalThis.eval is eval(). See the rule above it.',
    },
    {
      // Bare, for the same reason as `eval` above: `Function` here is a
      // binding reference, and a binding cannot be quoted. The property
      // spellings are the rule below.
      selector: "NewExpression[callee.name='Function']",
      message: 'new Function() compiles a string into a function, which is eval by another name.',
    },
    {
      // Bare for the same reason again.
      selector: "CallExpression[callee.name='Function']",
      message: 'Function() compiles a string into a function, which is eval by another name.',
    },
    {
      // `window.Function(s)` and `new window['Function'](s)`. The two rules
      // above key on `callee.name`, so neither of these reached them -- the
      // property route needs saying separately, as it does for eval.
      selector: eitherSpelling('MemberExpression', 'property', "'Function'"),
      message: 'window.Function is Function(), which compiles a string into a function.',
    },
    {
      selector: eitherSpelling('AssignmentExpression', 'left.property', '/^(inner|outer)HTML$/'),
      message: 'Assigning innerHTML/outerHTML parses the string as markup. Set textContent, or render it through React.',
    },
    {
      // `Object.assign(el, {innerHTML: s})` and any other object literal that
      // becomes an element's properties. The assignment rule above is the
      // statement form of the same sink.
      //
      // This also matches a destructuring *read* -- `const { innerHTML } = el`
      // -- which writes no markup. A known false positive with nothing to fire
      // on: `innerHTML` appears nowhere in `src` outside the test for these
      // rules. Narrow it only against a real read, not pre-emptively; on a
      // blocking gate the cost of a false positive is one line of argument and
      // the cost of a narrowing that goes too far is silence.
      selector: eitherSpelling('Property', 'key', '/^(inner|outer)HTML$/'),
      message: 'An innerHTML/outerHTML key parses its value as markup once the object reaches an element.',
    },
    {
      selector: eitherSpelling('CallExpression', 'callee.property', "'insertAdjacentHTML'"),
      message: 'insertAdjacentHTML parses its argument as markup, exactly as innerHTML does.',
    },
    {
      // Matched on the method name, not on `document`: `const d = document;
      // d.write(s)` is the same sink, and pinning the object let an alias
      // through. `writeln` is the same API one word along, and the message is
      // true of it verbatim. Nothing in `src/` calls `.write(`/`.writeln(` on
      // anything, so the wider match costs nothing; if something legitimate
      // ever does, narrowing it then is a decision someone makes on purpose.
      selector: eitherSpelling('CallExpression', 'callee.property', '/^write(ln)?$/'),
      message: 'write/writeln parse their argument as markup.',
    },
    // `dangerouslySetInnerHTML` reached three ways, and `react/no-danger` sees
    // only the first. Each needs its own selector -- a `Property` and an
    // `AssignmentExpression` are different nodes, so one does not imply the
    // other however alike they read.
    {
      // `<div {...{dangerouslySetInnerHTML: x}} />`, and any object literal
      // carrying the key on its way to becoming props.
      selector: eitherSpelling('Property', 'key', "'dangerouslySetInnerHTML'"),
      message: 'dangerouslySetInnerHTML injects unparsed markup. Nothing in this app needs it; see eslint.sinks.js.',
    },
    {
      // `props.dangerouslySetInnerHTML = x`, then spread. The one route that
      // is neither a JSX attribute nor an object literal, and the shape is
      // the one the innerHTML rule above already uses.
      selector: eitherSpelling('AssignmentExpression', 'left.property', "'dangerouslySetInnerHTML'"),
      message: 'dangerouslySetInnerHTML injects unparsed markup, assigned onto a props object as much as written in JSX.',
    },
  ],
}
