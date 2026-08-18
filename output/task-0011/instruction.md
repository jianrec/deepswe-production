Zustand ships middleware for persistence, immer, devtools and subscribeWithSelector, but has no first-party way to express derived (computed) state. Users repeatedly re-derive values inside selectors, which recomputes on every render and loses referential stability, or they hand-roll subscriptions that duplicate base state. We want a supported, composable solution that lives at both the store layer and the React layer.

Deliver two coordinated pieces sharing one memoization core:

1) A vanilla `computed` middleware exported from `zustand/middleware`. It wraps a state creator with a `compute(state) => C` function whose result is merged into the store state so computed keys are visible through `getState()`, selectors, and every existing consumer (`useStore`, `useStoreWithEqualityFn`). Computed keys are recomputed automatically whenever base state changes and are treated as read-only: if a user writes a computed key directly it must be recomputed and overwritten. Recomputation must run inside a single `setState` cycle so subscribers are notified exactly once per update (never twice). Each computed value must keep referential stability when its recomputed result is equal under the configured equality function (default `Object.is`), so downstream selectors and React components do not re-render spuriously. An optional `keys` list restricts which base-state changes trigger recomputation.

2) A `useComputed` React hook (importable from `zustand`) that derives a value from a store inside a component, memoizing across renders with an equality function (default shallow) so a component only re-renders when the derived output actually changes. It must be built on the same subscribe/getState contract used by `useStore` and be concurrent-safe via `useSyncExternalStore`.

Compatib...

Public API contract:
- export function computed<T, C extends object, Mps extends [StoreMutatorIdentifier, unknown][] = [], Mcs extends [StoreMutatorIdentifier, unknown][] = [], U = T>(initializer: StateCreator<T, [...Mps, ['zustand/computed', C]], Mcs, U>, compute: (state: T) => C, options?: ComputedOptions<C>): StateCreator<Write<T & U, C>, Mps, [['zustand/computed', C], ...Mcs]> — exported from 'zustand/middleware'
- export interface ComputedOptions<C extends object> { equalityFn?: (a: C[keyof C], b: C[keyof C]) => boolean; keys?: (keyof C)[] } — exported from 'zustand/middleware'
- StoreMutators augmentation: interface StoreMutators<S, A> { 'zustand/computed': WithComputed<S, A> } declared via `declare module '../vanilla'` inside src/middleware/computed.ts, where WithComputed<S, A> writes computed keys A onto getState/getInitialState/subscribe state and preserves setState overloads
- export function useComputed<S extends { getState: () => unknown; getInitialState: () => unknown; subscribe: (listener: (state: unknown) => void) => () => void }, U>(api: S, compute: (state: ExtractState<S>) => U, equalityFn?: (a: U, b: U) => boolean): U — importable from 'zustand'
- export { useComputed } re-exported from a new module src/computed.ts alongside `import { computed } from 'zustand/middleware'` (main-package import path `import { useComputed } from 'zustand'` must resolve)
- Default equality for useComputed is the existing shallow implementation exported by src/vanilla/shallow.ts (`shallow`); default equality for computed values is Object.is

Acceptance criteria:
- `computed(initializer, compute, options?)` is exported from `zustand/middleware` and merges the result of `compute(state)` into store state, visible via `getState()` and any selector.
- Computed values recompute automatically after every relevant `setState`, and the store notifies subscribers exactly once per update (no double notification caused by recomputation).
- Each computed value retains its previous reference when the recomputed result is equal under the configured `equalityFn` (default `Object.is`).
- Computed keys are read-only: a `setState` that writes a computed key is overridden by the recomputed value; the `replace: true` overload still re-merges computed keys.
- `options.keys` limits recomputation so it only runs when one of the listed base-state keys changes (shallow compared); omitting it recomputes on every update.
- `getInitialState()` returns state that already includes computed values so SSR hydration is consistent.
- `useComputed(api, compute, equalityFn?)` is importable from `zustand`, derives a value via `useSyncExternalStore`, and only triggers a re-render when the derived output changes under the equality function (default shallow).
- `computed` composes with `immer`, `persist`, `devtools`, `subscribeWithSelector`, and `combine` in any order without runtime errors, and its store mutator exposes computed keys in TypeScript selector/getState types.
- Computed keys are excluded from persistence unless explicitly included by the user's `partialize`.
- All 214 pre-existing tests under `pnpm test:spec` continue to pass with no changes to existing public API signatures.
