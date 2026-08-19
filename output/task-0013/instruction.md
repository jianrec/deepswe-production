The sorting, searching, and string-ranking algorithms in this repository each hard-code their comparison logic. `sorts/bubble_sort.py`, `sorts/insertion_sort.py`, `sorts/merge_sort.py`, and `sorts/selection_sort.py` only sort raw elements in ascending order; `searches/binary_search.py`, `searches/linear_search.py`, and `searches/jump_search.py` compare items directly; and `strings/top_k_frequent_words.py` bakes in its own frequency-then-lexicographic ordering. This makes the collection impossible to use for records, tuples-with-keys, or descending orders without wrapping data by hand.

Introduce a single, repository-wide ordering convention: every listed function gains keyword-only `key` and (where meaningful) `reverse` parameters that mirror the semantics of the Python built-ins `sorted`, `min`, and `bisect`.

Required behavior:
- `key`: an optional callable mapping each element to the value used for comparison. When `None` (the default) the element itself is compared, exactly as today.
- `reverse`: for sorting functions, produce a descending ordering; for `binary_search` and `jump_search` it declares that the input is sorted in descending order by `key` and inverts the comparison direction. `linear_search` scans unordered data and therefore takes only `key`.
- Sorting must be stable: elements comparing equal under `key` keep their original relative order, and this must also hold when `reverse=True`, matching `sorted(..., reverse=True)`.
- Searches return the index of the first element whose `key` equals the target's `key`, or `-1` when absent; a mismatch between `reverse` and the actual ordering yields `-1` rather than raising.
- `top_k_frequent_words` keeps ranking by descending frequency, but `key` customizes the tie-break among equal-frequency words (default: lexicographic ascending) and `reverse` flips the final list.

Compatibility requirements: calling any function with no new arguments must return byte-for-byte identical results and types to the current implementation, and all existing doctests must continue to pass unchanged. New parameters are keyword-only so positional call sites are unaffected. Passing a non-callable `key` must raise `TypeError`; searching with a `key` that produces non-monotonic va...

Public API contract:
- sorts/bubble_sort.py: def bubble_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- sorts/insertion_sort.py: def insertion_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- sorts/merge_sort.py: def merge_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- sorts/selection_sort.py: def selection_sort(collection: list, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> list
- searches/binary_search.py: def binary_search(sorted_collection: list, item: Any, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> int  # returns index or -1
- searches/linear_search.py: def linear_search(sequence: list, target: Any, *, key: Callable[[Any], Any] | None = None) -> int  # returns index or -1
- searches/jump_search.py: def jump_search(arr: list, item: Any, *, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> int  # returns index or -1
- strings/top_k_frequent_words.py: def top_k_frequent_words(words: list[str], k_value: int, *, key: Callable[[str], Any] | None = None, reverse: bool = False) -> list[str]
- Convention: key=None means compare elements directly; key callable maps element->comparison value; reverse=True means descending order (sorts) or descending-sorted input (binary/jump search); a non-callable key raises TypeError.

Acceptance criteria:
- Each listed function accepts keyword-only `key` and (except `linear_search`) `reverse` parameters with the defaults `key=None`, `reverse=False`.
- Default invocations (no new kwargs) return identical values and types to the pre-change implementations, and every pre-existing doctest passes without modification.
- `key=None` compares elements directly; a provided callable is applied to derive comparison values without mutating input elements.
- Sorting functions are stable under both ascending and descending (`reverse=True`) ordering, matching `sorted` semantics.
- `binary_search`/`jump_search` with `reverse=True` correctly locate items in descending-by-key input and return the item index, or `-1` when absent or when ordering does not match.
- `linear_search` supports `key` and returns the first matching index or `-1`.
- `top_k_frequent_words` ranks by descending frequency with a customizable `key` tie-break and honors `reverse`, defaulting to lexicographic ascending tie-breaks.
- A non-callable `key` argument raises `TypeError` in every function.
- Empty collections, single-element collections, and all-equal-key collections are handled without error across every function.
- `python -m pytest searches sorts strings -q` passes with no regressions.
