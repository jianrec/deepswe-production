The `searches`, `sorts`, and `strings` packages each compare elements ad-hoc: every algorithm hard-codes `<`/`>` on raw elements, so none of them can sort or search *by a derived key* the way Python's built-in `sorted`, `min`, and `bisect` allow via `key=`. There is also no shared, correct way to order strings the way humans expect ("img2" before "img10"), even though `sorts/natural_sort.py` hints at the need.

Build a small, opt-in ordering toolkit and wire it into a curated set of algorithms.

1. Add `sorts/comparator.py`, a dependency-free comparison core: an identity helper, a `key` resolver, a three-way `compare` returning -1/0/1 that honors an optional `key` callable and a `reverse` flag, and an `is_sorted` predicate over a collection under the same key/reverse semantics.

2. Add `strings/natural_key.py`, human/natural ordering primitives: split a string into alternating integer and text chunks, build a total-ordering sort key whose chunks are type-tagged so integers and text are never compared directly (must never raise `TypeError` on mixed inputs), and a three-way `natural_compare`. Case-insensitive by default, with an opt-in `case_sensitive` flag.

3. Extend a curated set of algorithms with keyword-only `key`/`reverse` parameters, delegating all element comparison to the new core: `sorts/merge_sort.py`, `sorts/insertion_sort.py`, `sorts/heap_sort.py`, `searches/binary_search.py`, `searches/jump_search.py`, and `searches/interpolation_search.py` (interpolation accepts `key` only, which must map elements to a number; it s...

Public API contract:
- sorts/comparator.py::identity(value: Any) -> Any
- sorts/comparator.py::resolve_key(key: Callable[[Any], Any] | None) -> Callable[[Any], Any]
- sorts/comparator.py::compare(a: Any, b: Any, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> int  # returns -1, 0, or 1
- sorts/comparator.py::is_sorted(collection: Sequence[Any], *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> bool
- strings/natural_key.py::natural_chunks(text: str, *, case_sensitive: bool = False) -> list[int | str]
- strings/natural_key.py::natural_sort_key(text: str, *, case_sensitive: bool = False) -> tuple[tuple[int, int | str], ...]
- strings/natural_key.py::natural_compare(a: str, b: str, *, case_sensitive: bool = False) -> int  # returns -1, 0, or 1
- sorts/merge_sort.py::merge_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- sorts/insertion_sort.py::insertion_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- sorts/heap_sort.py::heap_sort(unsorted: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- searches/binary_search.py::binary_search(sorted_collection: list, item: Any, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> int  # -1 when not found
- searches/jump_search.py::jump_search(arr: list, x: Any, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> int  # -1 when not found
- searches/interpolation_search.py::interpolation_search(sorted_collection: list, item: Any, *, key: Callable[[Any], float] | None = None) -> int | None

Acceptance criteria:
- `sorts/comparator.py` exposes `identity`, `resolve_key`, `compare`, and `is_sorted` with the exact signatures listed in the API contract.
- `compare(a, b)` returns -1/0/1 consistent with `<`, `==`, `>`; with `key`, it compares `key(a)` vs `key(b)`; with `reverse=True`, the sign is flipped for unequal values and 0 stays 0.
- `is_sorted(collection, key=..., reverse=...)` returns True exactly when adjacent pairs are non-decreasing (or non-increasing when reverse) under the resolved key; empty and single-element collections are sorted.
- `strings/natural_key.py` exposes `natural_chunks`, `natural_sort_key`, and `natural_compare`; `natural_sort_key` never raises on any string, and its chunks are type-tagged so int and text chunks never compare against each other.
- `natural_sort_key` is case-insensitive by default and case-sensitive when `case_sensitive=True`; sorting `['img10','img2','img1']` by it yields `['img1','img2','img10']`.
- `merge_sort`, `insertion_sort`, `heap_sort` accept keyword-only `key`/`reverse`; default calls are byte-for-byte behavior-identical to the base commit, including existing doctests.
- `merge_sort` and `insertion_sort` are stable under a `key`: equal-key elements retain input order.
- `binary_search` and `jump_search` accept keyword-only `key`/`reverse`, map the target `item` through `key`, return the found index, and return `-1` when absent.
- `interpolation_search` accepts keyword-only `key` (numeric-valued) and preserves its existing `None`-on-absent behavior.
- No existing public function signature is changed except by appending keyword-only parameters with defaults that preserve prior behavior.
- The full preflight suite `python -m pytest searches sorts strings -q` passes with the new doctests included.
