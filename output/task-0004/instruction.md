React.Children today exposes only `map`, `forEach`, `count`, `toArray`, and `only`. Application and library authors who need to fold over, select, or inspect an opaque `children` value are forced to first materialize `React.Children.toArray(children)` and then use Array methods. That is wasteful (an intermediate array is always allocated), and — more importantly — it loses the stable, collision-free traversal keys React computes internally, so downstream code that re-keys or reconciles selected children cannot reproduce React's own keying rules.

Extend the public `React.Children` namespace with four new functional traversal utilities that reuse React's existing single-pass child traversal (the same machinery that backs `map`/`forEach`/`toArray`):

- `React.Children.reduce(children, reducer, initialValue)` — fold over the flattened children in traversal order.
- `React.Children.filter(children, predicate, context)` — return a flat array of the surviving children, re-keyed exactly as `toArray` would key them.
- `React.Children.find(children, predicate, context)` — return the first child matching the predicate, or `undefined`.
- `React.Children.toKeyedArray(children)` — return `Array<{key, node}>` pairing every surviving child with the fully-qualified traversal key React assigns it.

The flattening rules must be identical to the existing utilities: `null`, `undefined`, and booleans are skipped; strings and numbers count as leaf children; nested arrays and iterables are flattened; the per-child `index` passed to callbacks is the running flattened index shared with `forEach`. Keys must match React's current algorithm bit-for-bit (element own-key escaping with the `$` prefix, base-36 positional keys, `:` sub-separators, `.` top-level se...

Public API contract:
- React.Children.reduce<T>(children: ReactNode, reducer: (accumulator: T, child: ReactNode, index: number) => T, initialValue: T): T
- React.Children.filter(children: ReactNode, predicate: (child: ReactNode, index: number) => boolean, context?: mixed): Array<ReactNode>
- React.Children.find(children: ReactNode, predicate: (child: ReactNode, index: number) => boolean, context?: mixed): ReactNode | void
- React.Children.toKeyedArray(children: ReactNode): Array<{key: string, node: ReactNode}>
- Named exports reduceChildren, filterChildren, findChild, toKeyedArray from packages/react/src/ReactChildren.js, aggregated into the Children namespace objects exported by packages/react/src/ReactClient.js and packages/react/src/ReactServer.js
- New module packages/shared/ReactChildKeyPath.js exporting: getElementKey(element: mixed, index: number): string, escapeUserProvidedKey(text: string): string, SEPARATOR: '.', SUBSEPARATOR: ':' — behavior-preserving relative to the prior inline ReactChildren implementation
- Traversal key format guarantee: top-level keys are prefixed with '.', nested levels joined with ':', user-provided element keys escaped and prefixed with '$', positional keys encoded base-36

Acceptance criteria:
- React.Children.reduce(children, reducer, initialValue) folds children in traversal order, invoking reducer(accumulator, child, index) with the same flattened index sequence forEach uses, and returns the final accumulator (initialValue when there are zero visited children).
- React.Children.filter(children, predicate, context) returns a flat Array of surviving children; element children are re-keyed with exactly the same keys React.Children.toArray assigns for the same positions, and non-element leaves (strings/numbers) are returned unchanged.
- React.Children.find(children, predicate, context) returns the first child whose predicate returns truthy (the original, un-rekeyed node) or undefined when none match, and stops traversal early after the first match.
- React.Children.toKeyedArray(children) returns Array<{key: string, node: ReactNode}> where key is the fully-qualified traversal key React computes internally (e.g. '.0', '.1:0', '.$userKey') and node is the corresponding child (elements carry the prefixed key, matching toArray).
- All four functions skip null, undefined, and boolean children, flatten nested arrays and iterables, and treat a single non-array child as a one-element traversal — identical to map/forEach semantics.
- Existing React.Children.map, forEach, count, toArray, and only produce byte-identical output and identical dev warnings before and after the refactor.
- Key-generation helpers are extracted into a shared module and consumed by both existing and new utilities; keys produced by the shared module are identical to those produced by the pre-change inline implementation.
- The four new functions are exported from every entry that re-exports Children (ReactClient.js / ReactServer.js and the package index) and are absent from the production bundle except as the four named exports.
- Passing a function or plain object as a child still triggers the same development-mode invalid-child warning path when traversed by the new utilities.
