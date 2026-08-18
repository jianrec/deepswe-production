Zustand ships several first-party middlewares (persist, devtools, redux, immer, subscribeWithSelector, combine) but has no built-in way to record a store's update history for undo/redo and time-travel. Applications that need editors, form wizards, or drawing canvases currently reach for third-party packages or hand-roll snapshot stacks that break when combined with immer or persist.

Add a first-party `history` middleware that transparently records past and future states of a store and exposes an undo/redo/time-travel API through a nested `temporal` store, plus React bindings that let components subscribe to the temporal state.

Behavior:
- `history(initializer, options?)` wraps a state creator like the other middlewares and registers the `'zustand/history'` store mutator. The resulting store gains a `temporal` property that is itself a full `StoreApi<TemporalState<T>>`.
- Every `setState` that changes tracked state pushes the previous (optionally `partialize`d) state onto `pastStates` and clears `futureStates`.
- `temporal.getState().undo(steps?)` restores prior states (merging into the main store) and moves the current state onto `futureStates`; `redo(steps?)` reverses this. `cle...

Public API contract:
- export function history<T, Mps extends [StoreMutatorIdentifier, unknown][] = [], Mcs extends [StoreMutatorIdentifier, unknown][] = [], U = T>(initializer: StateCreator<T, [...Mps, ['zustand/history', never]], Mcs, U>, options?: HistoryOptions<T>): StateCreator<T, Mps, [['zustand/history', never], ...Mcs], U>  // exported from 'zustand/middleware'
- export interface HistoryOptions<T, PartialTState = T> { limit?: number; equality?: (pastState: PartialTState, currentState: PartialTState) => boolean; partialize?: (state: T) => PartialTState; onSave?: (pastState: T, currentState: T) => void; handleSet?: (handleSet: StoreApi<T>['setState']) => StoreApi<T>['setState']; wrapTemporal?: (initializer: StateCreator<TemporalState<T>, [], []>) => StateCreator<TemporalState<T>, [], []> }  // exported from 'zustand/middleware'
- export interface TemporalState<T> { pastStates: Partial<T>[]; futureStates: Partial<T>[]; undo: (steps?: number) => void; redo: (steps?: number) => void; clear: () => void; isTracking: boolean; pause: () => void; resume: () => void }  // exported from 'zustand/middleware'
- declare module '../vanilla' { interface StoreMutators<S, A> { 'zustand/history': Write<S, { temporal: StoreApi<TemporalState<ExtractState<S>>> }> } }  // registers the temporal property on the store
- export function useTemporal<T, U = TemporalState<T>>(store: StoreApi<TemporalState<T>>, selector?: (state: TemporalState<T>) => U): U  // exported from 'zustand' (root)
- export function useTemporalWithEqualityFn<T, U = TemporalState<T>>(store: StoreApi<TemporalState<T>>, selector?: (state: TemporalState<T>) => U, equalityFn?: (a: U, b: U) => boolean): U  // exported from 'zustand/traditional'
- Runtime access point: `store.temporal` (and `useBoundStore.temporal`) is a `StoreApi<TemporalState<T>>` whose `getState()` returns the current temporal state object described above.

Acceptance criteria:
- `import { history } from 'zustand/middleware'` works and `history` can be applied via `create<T>()(history((set) => ({...})))` and `createStore<T>()(history(...))`.
- A store created with `history` exposes `store.temporal` that is a `StoreApi<TemporalState<T>>` with independent `getState`/`setState`/`subscribe`.
- After N distinct `setState` calls, `store.temporal.getState().pastStates.length` equals N (subject to `limit`) and `futureStates` is empty.
- `undo()` restores the immediately preceding state into the main store and appends the pre-undo state to `futureStates`; `undo(k)` performs k steps atomically.
- `redo()` re-applies the most recently undone state and pops it off `futureStates`; issuing a new tracked `setState` after an undo clears `futureStates`.
- `clear()` empties both `pastStates` and `futureStates` without changing the main store's current state.
- `pause()` sets `isTracking` to false and stops recording; `resume()` restores recording; states changed while paused are not added to `pastStates`.
- `limit: L` keeps at most L entries in `pastStates`, discarding the oldest first; unset `limit` keeps unbounded history.
- `partialize` restricts which keys are recorded and restored; `equality` skips recording when the tracked slice is unchanged; `onSave` is invoked once per recorded change.
- `history` composes with `immer`, `persist`, `subscribeWithSelector`, and `devtools` in either wrapping order without runtime errors, and undo/redo trigger normal store subscriptions.
- `useTemporal(store.temporal, selector)` re-renders a React component only when the selected temporal slice changes; `useTemporalWithEqualityFn` honors a custom equality function.
- TypeScript infers `store.temporal` and `TemporalState<T>` with no explicit generics, and the existing middleware mutator-chain type tests still pass.
- All 214 pre-existing tests continue to pass under `pnpm test:spec` with no changes to their behavior.
