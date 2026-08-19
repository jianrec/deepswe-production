# PR Chain

## 1. 1

Depends on: []

Modules: sorts

Files: sorts/bubble_sort.py, sorts/insertion_sort.py, sorts/merge_sort.py, sorts/selection_sort.py

Behavior: Establish the shared key/reverse convention and implement it in the four sorting algorithms with guaranteed stability under both directions; preserve all existing signatures, defaults, and doctests.

## 2. 2

Depends on: [1]

Modules: searches

Files: searches/binary_search.py, searches/linear_search.py, searches/jump_search.py

Behavior: Add key support to all three searches and reverse (descending-input) support to binary_search and jump_search, returning matching indices or -1; keep default behavior identical.

## 3. 3

Depends on: [1]

Modules: strings

Files: strings/top_k_frequent_words.py

Behavior: Route the ranking through the shared ordering convention: descending frequency with a customizable key tie-break and reverse flag, defaulting to lexicographic-ascending ties.

## 4. 4

Depends on: [1, 2, 3]

Modules: sorts, searches, strings

Files: sorts/bubble_sort.py, searches/binary_search.py, strings/top_k_frequent_words.py

Behavior: Harden edge cases and error handling across modules: non-callable key raises TypeError, empty/single/all-equal inputs are safe, and docstrings document the convention uniformly.
