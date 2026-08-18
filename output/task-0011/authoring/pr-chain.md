# PR Chain

## 1. 1-memoization-core

Depends on: []

Modules: vanilla, shallow

Files: src/vanilla/shallow.ts, src/vanilla.ts

Behavior: Add a small internal per-key memoization helper (createComputeMemo) that, given a compute function and an equality function, produces a merger that recomputes derived values and preserves references for unchanged results. Reuse the existing `shallow` function for object comparison. Expose only internal types needed by the middleware and hook; no change to existing public vanilla API surface or its runtime behavior.

## 2. 2-computed-middleware

Depends on: ['1-memoization-core']

Modules: middleware

Files: src/middleware/computed.ts, src/middleware.ts, src/types.d.ts

Behavior: Implement the `computed` middleware: wrap the store's setState so that after each base update it recomputes derived values (respecting options.keys and options.equalityFn), merges them read-only into the next state, and notifies subscribers once. Seed computed values into the initial state so getState/getInitialState include them. Declare the 'zustand/computed' StoreMutators entry and export `computed` and `ComputedOptions` from src/middleware.ts. Handle replace:true, function updaters, and object updaters.

## 3. 3-react-hook

Depends on: ['1-memoization-core']

Modules: react, shallow

Files: src/react/computed.ts, src/react.ts

Behavior: Implement `useComputed(api, compute, equalityFn = shallow)` on top of useSyncExternalStore, memoizing the derived value between renders and only producing a new reference when the equality check fails. Re-export `useComputed` from src/react.ts so it is available from the main `zustand` entry.

## 4. 4-exports-and-composition

Depends on: ['2-computed-middleware', '3-react-hook']

Modules: root, react

Files: src/computed.ts, src/index.ts

Behavior: Add src/computed.ts mirroring src/shallow.ts to co-locate `computed`/`useComputed` re-exports, ensure src/index.ts surfaces `useComputed`, and verify composition with immer/persist/devtools/subscribeWithSelector/combine plus persist partialize exclusion of computed keys. No breaking changes to existing exports.
