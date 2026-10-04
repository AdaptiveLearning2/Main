# Frontend

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules this file cites, and the reporting rules every
surface obeys. Read those first.

**Read this file when you are touching** anything under `Website/AdaptiveLearning/frontend`: a page,
component or hook, `lib/api.js` or `AuthContext`, a chart, a test or the doubles in `src/test/`, the
lint configs, or a Tailwind colour.

## Frontend tests mock through `src/test/`, not a hand-rolled `vi.fn()`

`src/test/mocks/apiFetch.js` and `src/test/mocks/supabase.js` are the shared doubles, reached by
pointing the factory at the file so the mocked module and the handle driving it are one instance:

```bash
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
```

**`apiFetch`'s double is a router, and an unmatched path throws.** Most pages fetch two to four
endpoints in parallel on mount and the interesting tests need *one* to fail; `mockResolvedValueOnce`
chains express that by the order `Promise.all` happens to start them in, so such a test passes for a
reason unrelated to what it claims and breaks when a page adds a fetch. `mockApi({...})` registers
the happy path, `overrideApi(path, fn)` layers one failure over it. Throwing on an unrouted path is
load-bearing: a silent `undefined` reaches a page as a successful read of nothing, which is the
state most of this suite exists to tell apart from a failure. **Method-scoped routes are tried
before methodless ones**, whatever order they were written in — first-match-wins alone made
`{'/api/x': …, 'PUT /api/x': …}` answer the write with the read. Reset with `resetApi()`, never
`mockReset()`, which drops the implementation and every route with it.

Mocking `lib/supabase` as a *module* also sidesteps its import-time throw on missing
`VITE_SUPABASE_*`, the normal state under `vitest` (CI supplies those to the build step only).
`fireAuthEvent(event, session)` reaches the properties that only exist post-mount: the `SIGNED_OUT`
cleanup an expired refresh token triggers with nobody calling `signOut()`, and the
`TOKEN_REFRESHED` handling that must not await anything reading the session.

**`lib/api.test.js` is the one place `apiFetch` runs for real**, with only `fetch` and
`lib/supabase` mocked. Every other test replaces it wholesale, so nothing otherwise exercises the
URL it builds, whether the bearer is attached, or how a non-2xx becomes an `Error` carrying
`.status`. Fixtures live in `src/test/fixtures/` as **builders**, not constants
(`buildWeeklyReport`, `buildConsentState`, `buildChartArchive`, …): every interesting case is one
field off the happy path, and a test that restates a whole payload to move one field tends to move
two. `CHANNEL_REASONS` there is the `offLabel` four-state matrix, named for the state each input
must produce rather than for its field values.

**A fixture of one cannot tell identity from presence.** A test asserting *which* row, element or
record something resolved to needs a sibling in the fixture to be wrong about: against a
one-question bank, `Questions.test.jsx`'s focus-restore test could not separate "focus returned to
the row that opened the modal" from "focus returned to the first `<button>` on the page", and a
`useDialog` handing focus to `querySelector('button')` passed it. Rule 4 in its other direction —
there the fixture repeats the code's misreading, here it is too small to contradict anything — and
only the mutation check finds either. **Give the shared fixture the sibling**, rather than
registering one per test: a local override needs a reason for why the shared one cannot have it,
and the reason is easy to get wrong (this one claimed to protect pagination tests the file does not
contain).

**`asyncUtilTimeout` is 5000 in `src/test/setup.js`, not Testing Library's 1000.** That default is
chosen for pure components; a query for something that legitimately arrives on the *second* 5 s poll
races a budget unrelated to what it waits for, and only passed because the machine was idle. Under
the full run the same query misses by a few hundred milliseconds. Raising it costs nothing on a
passing assertion, since `waitFor` returns as soon as the condition holds.

**A timeout does not fix an assertion anchored to elapsed time.** One contact-hint test slept 6 s
and asserted "exactly one 5 s poll has landed" — true only if the interval was in the right part of
its cycle; land two and the hint is correctly on screen and the assertion fails against working
code. It now waits for the *read count* to advance by one, which gives it a whole poll of slack.

**`AdaptiveReconnect.test.jsx` runs on real timers, and every fake-clock version hung.** The pairing
sequence is a chain of 1–1.5 s waits noticed by a 5 s poll; under `vi.useFakeTimers()` — with or
without `shouldAdvanceTime` — `await act(async () => advanceTimersByTimeAsync(…))` never resolved.
Each of those tests costs 10–20 real seconds and declares a 60 s timeout. `Overview.test.jsx`'s
fake-clock pattern works for a 300 ms debounce and did not survive this component.

**Clear persisted view state in `beforeEach`.** `viewPrefs.js` and `al_sidebar_collapsed:<scope>`
write to `localStorage`, which jsdom keeps for the whole file — so every test declared *after* one
that flips a switch renders with it already flipped, silently, which reads as one of the later tests
being broken. `StudentReport.test.jsx` and `layoutAccessibility.test.jsx` are where that bites —
the latter's account describe collapses the sidebar in its **first** test, so the two after it fail without the
clear. `clearViewPrefs()` is the guard, and it needs a test standing **downstream of the
leak** to have teeth: with the switching test last in the file, removing the guard breaks nothing.

## The frontend reads the same column, through `GET /api/profile/me`

The claim is wrong for any role not chosen at sign-up — an account promoted to `admin` in the SQL editor has no `role`
in its metadata — so `AuthContext` takes its role from the backend, the source `AdminGuard` asks.

- **It is not in the `onAuthStateChange` callback.** `apiFetch` calls `getSession()` for the token, supabase-js holds
  an auth lock while dispatching, and awaiting it there deadlocks — the app hangs on a loader for ever. It lives in an
  effect the callback merely schedules.
- **Keyed on the user *id*, not the user object**, so a token refresh mid-lesson does not put the whole app back
  through a loading state.
- **A failed read falls back to the claim, not to `student`.** A blip is not a demotion; defaulting to the
  least-privileged role would drop every teacher into the wrong application whenever the API was down. The opposite
  direction to `_role` on the backend, deliberately: that one decides *access*, this one decides which nav to draw.

`loading` stays true until the role resolves, or the guards see `role === null` for a frame and render "this account
isn't set up" on every page load. **That makes this the one request in the app that may not hang, so it is the one that
passes `timeoutMs`.** A request that *fails* is caught; one that never settles leaves `loading` true for ever — an
infinite loader for every signed-in user. **A `.catch` is not a bound.** `timeoutMs` is **opt-in with no default**,
because a blanket one would abort `/api/students/{id}/learning-strategies`, which is bounded server-side and can queue
behind other waiters first. The bound covers the **whole call**, not the `fetch`: `getAccessToken` awaits
`supabase.auth.getSession()`, which goes to the network when the token needs refreshing, so wrapping `fetch` alone
leaves exactly the hang it was added to stop.

Login and Register navigate to `/` and let `HomeRedirect` choose: `homeRoute.js` is the only role-to-home map. The claim
survives only as the fallback, and nothing that matters may be gated on it.

## A cached read is opt-in, per account, and dropped on sign-out

`apiFetch(path, { cache: true })` reuses a result, or the request in flight, for 30 s; a failure is never kept, every
caller gets its own copy, and any write invalidates every cached read under its top-level resource (`/api/classes/…`),
both before and after it lands. Entries are keyed by account, and both sign-out paths call `clearApiCache()` beside
`clearViewPrefs()`, so a shared school computer's next account starts with nothing cached.

## A name comes from `displayName`, never from the email

`AuthContext` exposes `displayName` (stored name → claim → email prefix → null) and no surface derives its own: sign-up
seeds the stored name from the email prefix, so the two agree only until the first edit. **A save calls
`refreshProfile()`**, and a blank stored name is `null`. `profiles` has no `username` column.

**A parse error drops a file's tests from the run rather than failing them**, so count test **files** as well as tests.
A `useAuth` double carries what the real one carries, `refreshProfile` included.

## Every chart goes through `AccessibleChart`, and a test enforces it

Recharts emits bare `<svg>` with no accessible name and nothing a screen reader can walk, so a chart rendered directly
announces as nothing at all. `components/charts/AccessibleChart.jsx` is the only place that may render one.

**The `sr-only` data table is a *sibling* of the `role="img"` wrapper, never a child.** WAI-ARIA's
presentational-children rule prunes every descendant role from an `img`, so a nested table is invisible to real
assistive technology — while being **perfectly visible to a jsdom test**, because Testing Library reads DOM attributes
rather than modelling the accessibility tree. That is the trap, and it is the opposite way round from how it first
reads: the table is not what the test cannot see, it is the *pruning*. `getByRole('table')` finds the element whether
or not a real reader would, so a hand-assembled call site has nothing to fail against, in the browser or in CI. That is
why this is a component rather than a documented recipe, and why `AccessibleChart.test.jsx` walks the source and fails
on any chart component rendered outside it. **It cannot see a hand-written `<svg>`** — the honest limit of a source
check, and where `Heatmap.jsx`, `BarGraph` and the cohort roster table sit.

**One `columns` spec drives the sentence and the table.** They were separate literals for one PR and disagreed twice in
it: a key named `bpm` where the rows carry `heart_rate_bpm`, so a visibly-plotted line announced "not recorded"; and
raw 0..1 ratios described with a `%` unit, announcing a session ranging 42–78% as "Focus 0% to 1%". Neither is visible
on screen and no test could catch them, because both surfaces were wrong in the same way at once.

**Scaling belongs in the spec, per page, because the pages differ.** `SessionReview` and `Live` plot raw ratios
against `domain={[0, 1]}` and need `scale: asPercent`. `SignalPanel` scales **the fields it names** into its chart data
and spreads every other field across untouched, so a column for one of those others needs a `scale` like anywhere
else. "This page scales on the way in" is the wrong unit of thought and has already cost one bug.

**A column must name a series the chart actually draws** — including when the `<Line>` is conditional, in which case
the column is too. A screen-reader user given a series no sighted reader can see has a different report, not an
equivalent one. Found **twice**: `SignalPanel`'s `engagement` column had no `<Line>` at all, so nothing on screen could
contradict its wrong scaling; `SessionReview` then kept `heart_rate_bpm`/`rmssd_ms` columns whose lines are gated on
`hasHeart`, so a session with no headband emitted "Heart rate: not recorded" on every row. The first fix was applied
where it was found rather than swept for siblings. **Check what the chart plots, and under what condition, before
copying a spec across.**

### A reader can hide a series, so the one-list rule is structural

`SeriesFilter` + `useSeriesFilter` put toggles above three line charts. **Two of them reach a parent as well as a
teacher**, because `StudentProgressReport` is shared by `teacher/StudentReport` and `parent/ChildDetail`: anything
added to those panels lands on both routes. Fine here — the control draws less, never more — but it is the question to
ask of the next thing added, and `viewerRole` is how a panel differs between the two.

Each chart declares **one list per series** (`key`, `label`, `unit`, `scale`, `colour`, `axis`, `name`) and derives the
`<Line>`s, the `columns` spec and the chips from it. That is the point rather than tidiness: "a column must name a
series the chart draws" was a thing to remember while the only gate was `hasHeart`, and it becomes a thing a teacher
does at will — a hand-wired column goes on announcing *"RMSSD: not recorded"* on every row of a session that recorded
it fine and was simply not being shown. Four things follow:

- **The colour is read from the same entry the line is stroked with**, applied inline rather than as a Tailwind class —
  the chip cannot drift from what it names, and a `bg-${…}` would ship no rule at all. **The backend reads that list
  too**: `test_chart_render.py` scrapes `colour` out of it to check the archived SVGs still use the palette the app
  drew, so moving those colours breaks a *Python* test. Its scraper refuses an empty result, because a shape change it
  cannot read is otherwise a check that passes while seeing nothing.
- **An axis mounts only while a *shown* series uses it**, and anything referencing an axis — `SessionReview`'s answer
  markers and its failover lines — is gated the same way. Recharts throws on a line naming an axis that is not there.
- **Everything off says so and offers *Show all*, rather than the last toggle refusing to move.** A control that
  silently does nothing is harder to understand than an empty chart that explains itself, and the message is distinct
  from "no history yet" and "could not be loaded": those are claims about the data, this is a claim about the view. The
  chart is not rendered there at all, so there is no empty axis and no column-less table.
- **No toggle for a series that cannot be drawn** — a control whose only outcome is the state already on screen.

**The hook stores what is *hidden*, and takes no series list.** Both halves are load-bearing. Storing the hidden keys
is what draws a series that becomes available *later*: these charts gain series as data resolves, and a shown-set
snapshotted at mount leaves the newcomer switched off with nothing explaining why. Taking no list is what keeps it
callable above `SessionReview`'s `loading` and `err` early returns — `hasHeart` is derived from loaded rows far below
them, so a hook needing the list was a conditional hook call and threw on all 28 tests in that file. The selection is
per mount and deliberately not persisted.

**Test it on `columnheader`, never on the summary sentence** — `describeSeries` drops a series with no readings on its
own, so an aria-label assertion passes whether or not the column is gated. And a `getByText('Focus')` that used to be
unambiguous now matches the chip *and* the table header; assert the role rather than loosening the query.

### Categorical charts, and the sampled table

**A categorical chart is `sliceSpec(label, rows, noun, {nameKey, valueKey, rowLabel})`, spread into the component.** It
returns the sentence, the rows and the columns together so the noun is written once — it names what the values count in
the sentence and heads the table column. As two literals they drifted: `Analytics` built its sentence from a remapped
`topicData.map(d => ({name, value}))` while its table read `topicData` with key `count`.

The table is **sampled to 60 rows** and says so in its caption. A 4 Hz channel over an hour is ~14,000 rows, built on
every render for a table nobody sighted sees — and unusable for those who do. A silently shortened one would claim the
session was shorter than it was. `sample()` returns the rows alone; "was it sampled" is
`tableRows.length < (rows?.length ?? 0)` at the one place that asks, since two return values could disagree. Note the
`?? 0`: `sample()` guards a nullish `rows` internally, so deriving the flag *outside* it moved that check away from the
guard and crashed on a comparison. **Moving a derivation out of a function moves it out of that function's guards.**

## A backend poll goes through `usePoll`

`hooks/usePoll.js` never overlaps calls — the next is scheduled when one settles — pauses while the tab is hidden
(the next call keeps its due time), and doubles its wait after a throw up to `maxBackoffMs`. A `setInterval` poll
stacks requests behind a slow backend and runs all night in a background tab. **`pauseWhenHidden: false` only where
the poll keeps something alive**: under pull, `Adaptive.jsx`'s status poll is what holds the station pairing
(`PAIRING_IDLE_SECONDS`). The lesson page's intervals live in `pages/student/pollIntervals.js` (30 s under push, where
both answers are configuration); teacher Live polls at 2 s, backing off to 30 s. `immediate: false` waits an interval
before the first call, for a value the page has just read. Three `setInterval` polls remain, all in `Adaptive.jsx` and all
running while hidden: the headband telemetry (the sidecar under push; under pull the status poll's latest answer, read
afresh only while reconnecting), the sidecar's push status, and the `VITE_EEG_DEBUG` readout.

## `set-state-in-effect` is cleared, and the shapes that cleared it are worth reusing

Where the state is a reset driven by a prop changing — an acknowledgement cleared when enforcement resumes, a pulse
started by a new timestamp — adjust it *during render* against a `useState` holding the previous value, which React
re-runs before painting. Where it is a `loading` flag around a fetch, don't store one: keep the key the data in hand
belongs to (`loadedFor`) and derive `loading = loadedFor !== id`, so switching session or class raises the skeleton on
the render that changes the id and no previous subject's charts can be painted under this one's heading. A flag raised
by a *user action* stays a flag — `Sessions.jsx` sets it in the class selector's `onChange`, which is an event handler
and not an effect.

Both shapes have since bitten, and the corrections are the load-bearing half:

- **Derived `loading` needs a remount, not just a derivation.** `loading = loadedFor !== id` reads *false* when you
  navigate A→B→A: B's request is cancelled on the way out without ever advancing `loadedFor`, so returning to A finds
  it still saying `'A'`. `SessionReview.jsx` therefore keys the body on the id (`<Body key={sessionId} …>`), which
  resets all session-scoped state at once, the `err` that let a failure on A mask a B that loaded fine included.
  `ChildDetail.jsx` and `ClassDetail.jsx` do the same: the pattern for any page whose state belongs to one route param.
- **The compiler behind these rules skips a component it cannot build**, and `react-hooks/todo` (on) reports it: a
  `finally`; a `throw`, `?.`, `||` or ternary inside a `try`; `??=`; an inline arrow as a default parameter. Use a promise
  chain: its `.finally` runs even when the catch throws, and an effect's loader needs one (state after an `await` counts).
- **The render-time adjustment compares against the previous *render*, and that is not always the question.**
  `useValueChange` (`hooks/useValueChange.js`) is the extracted form and is right for `Flags.jsx`. It was wrong for
  `FlowDot.jsx`, which needs the last value it *acted on*: the pulse timer clears the live state, so a timestamp that
  goes transiently null and comes back unchanged reads as a change and flashes "fresh data" for data that is not new.
  Keep the acted-on value in its own state that nothing else clears. **A hook parameter nobody reads is the tell.**
- **Deriving state does not remove the need to cancel.** Every fetch that can be superseded needs a guard, and the
  slow ones are where it matters: on `Sessions.jsx` a slow class read let the previous class's response land last
  and repaint the list under the new class's name. It guards with `useLatestRequest` rather than a cleanup flag,
  because the effect is not the only caller — the retry button is the other, and a retry is exactly when someone
  changes class rather than waiting.

## Two rules from `eslint-plugin-react` are on, and both have to stay on

`no-unused-vars` cannot see JSX, so without `react/jsx-uses-vars` every identifier used *only* inside markup — an
`icon: Icon` prop rendered as `<Icon />` — is reported as an unused import, and the false hits bury the real ones. The
plugin's `recommended` config is deliberately **not** extended: its large ruleset would turn a clean run red.

`ignoreRestSiblings: true` goes with it, for the destructure-to-omit idiom (`const { x, ...rest } = obj` to build an
object *without* `x`, which is how the tests construct a payload predating a field). The binding is unused by design;
deleting it to satisfy the rule would put the key back.

With both, **`no-unused-vars` is clean and therefore load-bearing** — a hit is real dead code, so fix it.

The second is **`react/no-danger`**, which arrives with the XSS sinks below rather than from the plugin's
`recommended` config; that one is still not extended, for the reason above.

## The XSS sinks are a second lint run, independent of the first

`npm run lint:sinks` (`eslint.sinks.config.js`, CI step *Lint XSS sinks and motion features*) extends **no** shared
config, so it is red if and only if a sink (or a motion misuse, below) was added, whatever happens to
`js.configs.recommended` or the two react plugins the main run extends. Both runs block; kept apart, no rule change
elsewhere can switch the security gate off.

`eslint.sinks.js` exports them and **both configs import it** — the main one for editor feedback, the gate for CI.
Two literals would drift, and the copy that drifts is the one nobody runs locally.

**This is what lets the Supabase JWT stay in `localStorage`.** That is acceptable only while nothing in the app can
execute injected markup, so the rules cover `dangerouslySetInnerHTML` (three ways: the JSX attribute via
`react/no-danger`, an object-literal key, and a member assignment — different AST nodes, so one selector does not
imply another), `eval`/`window.eval`, `Function`/`new Function`, `innerHTML`/`outerHTML` both assigned and as an
object key, `insertAdjacentHTML`, and `write`/`writeln` matched on the method rather than on `document`, since an
alias is the same sink. Treat this, the CSP, and the absence of a markdown or LaTeX renderer as one mitigation.

**A name has two spellings ordinary style produces, and they are different nodes**, so no selector here may be
written bare for a property: `el.innerHTML` and `{innerHTML: s}` put the name in an Identifier's `.name`,
`el['innerHTML']` and `{'innerHTML': s}` put it in a string Literal's `.value`. They go through `eitherSpelling`,
which emits both: a hand-written pair misses one, and `<div {...{'dangerouslySetInnerHTML': {__html: x}}} />` then
passes the gate. `callee.name` is the one exemption, for `eval`/`Function` as bindings, since a binding reference
cannot be quoted.

**A template-literal computed key is a third spelling and is not covered**, deliberately: nobody writes
``el[`innerHTML`]`` by accident, and anyone writing one on purpose can defeat the gate with a disable comment
instead. So this covers the spellings ordinary style produces, not every spelling the grammar allows — and the list
above is what the rules cover, not a claim of closure.

**Reading the rules cannot show a missed spelling, so `src/test/sinkRules.test.js` reads the report**: it runs ESLint
over source text with the same `eslint.sinks.config.js` CI uses and asserts each of 25 spellings is flagged. Its
other half asserts six ordinary forms are *not*, so the first half cannot be satisfied by a selector matching
everything. Both halves are load-bearing and both were checked by breaking them. The lint script still has to
exist: the test proves the rules catch the forms, the script proves they are applied to the tree.

Two config details are load-bearing, both found by the gate failing on code it has no opinion about: it registers
`react-hooks` **without enabling any of its rules**, because an `eslint-disable` naming a rule no config defines is
itself an error (five, in source files); and it sets `reportUnusedDisableDirectives: 'off'`, because every disable
in the tree is for a rule this run does not have.

**The same gate holds the motion guard** (`eslint.motion.js`). App.jsx loads `domAnimation` into `<LazyMotion>`,
so `motion` from `framer-motion` or `motion/react`, anything from either `*/client`, a dynamic import of
any of them, and a drag, pan or layout prop on an `m.` element (inert without `domMax`) are errors;
`motionImports.test.js` derives that prop list from motion-dom's types. `src/test/setup.js` renders every test inside
`<LazyMotion strict>`, so `m` animates as it does live and a stray `motion` throws. **App itself is not `strict`**:
both guards run before merge, and live a miss would blank the app where it otherwise costs ~13 KiB. A test about
App's own `LazyMotion` takes the real render through `vi.importActual`, or the wrapper supplies what it tests.

## Muted text is `text-gray-600 dark:text-gray-400`, and a test does the arithmetic

Contrast is one of the few accessibility properties a source check can settle outright, so
`src/test/contrast.test.js` computes it rather than trusting a convention. Measured against the surfaces this
app paints (Tailwind 3.4 stock `gray`):

| | best surface | worst surface | AA 4.5 |
| --- | --- | --- | --- |
| light `gray-400` | 2.54 on white | 1.72 on gray-300 | fails everywhere |
| light `gray-500` | 4.83 on white | 3.28 on gray-300 | fails from gray-100 down |
| light `gray-600` | 7.56 on white | 5.13 on gray-300 | passes |
| dark `gray-500` | 4.16 on gray-950 | 2.13 on gray-700 | fails everywhere |
| dark `gray-400` | 7.93 on gray-950 | 4.06 on gray-700 | passes except on gray-700 |

**The dark half has to be added, not just the light half darkened.** 138 of the 146 sites named no `dark:`
variant at all, so they rendered gray-400 in *both* modes — where it already passes. A straight
`gray-400 → gray-600` substitution would have fixed light mode by breaking dark mode.

**A dark class in one ternary branch says nothing about the grey in another**: in
`${on ? '… dark:text-indigo-300' : 'bg-gray-100 text-gray-400 dark:bg-gray-800'}`, darkening the grey branch without
a companion takes dark mode from 5.78 to **1.94**. Resolve each branch separately, and model the fallback: an element
with no `dark:text-` renders its bare colour in dark mode too.

**Compute the ratio, never match a class name**: a grep for `dark:text-gray-500` passes `dark:text-gray-600`, which is
worse (2.35), and the ternary regression above.

`text-gray-500` on white is fine at 4.83 and is left alone; it is only wrong on a `bg-gray-100` card (4.39). A
bare grey with **no** dark companion is a separate failure the same-element check cannot see — it renders
gray-500 on the gray-900 card at 3.67 — so the last test asks whether *the file* ever paints a dark surface.
Coarse on purpose: it separates a page whose cards flip from `MainLayout`, the permanently-white marketing
shell, where adding a companion would put gray-400 on white at 2.54. **Where the background comes from a
parent, no source check can see it.**

**`Adaptive.jsx`'s debug readout is the only exemption.** It paints `bg-gray-950` with no `dark:` prefix, so it
is dark in both modes and every rule above reverses inside it: gray-400 passes at 7.93 and gray-600 would be
unreadable. Its `gray-500` was raised *to* gray-400. Check for an unprefixed dark background before assuming a
grey is too light.

`text-[10px]` (36 uses) is **not** a contrast failure — WCAG sets no minimum font size — so it was left alone.
What mattered was the combination, and the tiny badges that were also sub-AA are fixed.

## A tone is a whole class name

The three consent notices (`ChildWithdrewBanner`, `ParentRestoredBanner`, `ParentLinkedBanner`) share
`NoticeBanner`, which owns what each restated: **a failed acknowledgement leaves the banner standing**, since a
notice that dismisses itself on a failed write is one nobody sees again. `onAcknowledge` clears whatever made the
banner render; the shell owns the pending flag, swallows the rejection, and clears `busy` on both paths, or an
acknowledgement that does not unmount the banner leaves its button dead.

**Tone classes are full strings in a map, never interpolated.** Tailwind decides what CSS to ship by scanning
source text for complete class names, so `bg-${tone}-50` renders markup pointing at a rule that was never
generated — a banner with no background at all, **in production only**, since the dev server is not what does
the scan. No test can catch it either: the rendered class string is identical and jsdom has no stylesheet.
Source review is the only check, which is why the map exists. The same applies to `Heatmap.jsx`'s colour scale,
which is a map of complete class strings rather than an interpolation.
