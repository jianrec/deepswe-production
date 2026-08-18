`fd` currently emits results in filesystem/traversal order, which is nondeterministic because the directory walk is parallelized. Users who pipe `fd` into scripts, snapshots, or `--exec` pipelines frequently need a stable, predictable ordering. Add a first-class sorting facility.

Introduce a new `--sort <key>` option. The accepted keys are `path`, `name`, `size`, `modified`, `accessed`, `created`, `extension`, and `none`. `none` is the default and MUST preserve today's streaming behavior exactly (no buffering, results printed as discovered). Any other key switches `fd` into buffered mode: it collects every matching entry from all search roots, sorts the full set, and only then produces output (or runs commands).

Ordering rules must be a *total, deterministic* order so identical inputs always yield identical output regardless of thread count:
- `path`: compare the entry's full path by raw OS bytes.
- `name`: compare the file name (basename) by raw bytes, then break ties by full path.
- `size`: compare file byte length numerically (not lexicographically); entries lacking metadata are treated as size 0; ties break by path.
- `modified`/`accessed`/`created`: compare the corresponding `SystemTime` ascending (oldest first); entries whose timestamp is unavailable on the platform sort as the UNIX epoch; ties break by path.
- `extension`: compare the file extension by raw bytes with entries lacking an extension ordering first, then break ties by name, then by full path.

Add a `--reverse` flag that reverses the final sorted order. `--reverse` REQUIRES `--sort` (with a non-`none` key) and must produce a clap error otherwise. Sorting must apply *before* `--max-results` truncation, so `--sort size --max-results 3` returns the three smallest files, not the first three discovered. When `--exec`/`--exec-batch` is combined with sorting, commands (and batch argument order) must run in the sorted order. `-l/--list-details`, color output, null separators, hidden/ignore filtering, and exit codes must be unaffected other than ordering.

Compatibility: without `--sort`, behavior, buffering, and performance must be byte-for-byte identical to the base commit. Smart-case matching is unrelated to sorting; sorting comparisons are case-sensitive/byte-wise. Invalid `--sort` values must produce a clap usage error listing valid keys.

Public API contract:
- CLI flag: `--sort <SORT>` where SORT is one of {path, name, size, modified, accessed, created, extension, none}; default = none.
- CLI flag: `--reverse` (long only, boolean) which requires `--sort` with a non-`none` key.
- src/sort.rs: `#[derive(Clone, Copy, Debug, PartialEq, Eq, Default, clap::ValueEnum)] pub enum SortBy { #[default] None, Path, Name, Size, Modified, Accessed, Created, Extension }`
- src/sort.rs: `pub fn compare(a: &crate::dir_entry::DirEntry, b: &crate::dir_entry::DirEntry, sort_by: SortBy) -> std::cmp::Ordering`
- src/sort.rs: `pub fn sort_results(entries: &mut Vec<crate::dir_entry::DirEntry>, sort_by: SortBy, reverse: bool)`
- src/config.rs: `pub sort: crate::sort::SortBy` field on `Config`
- src/config.rs: `pub sort_reverse: bool` field on `Config`
- src/dir_entry.rs: `pub fn extension(&self) -> Option<&std::ffi::OsStr>` on `DirEntry`
- Observable behavior: with a non-`none` sort key, `fd` buffers all results (ignoring `max_buffer_time`) and prints them in the total order defined by `compare`, optionally reversed.

Acceptance criteria:
- `fd --sort name` prints matching entries ordered by basename with full-path tie-breaking, deterministically across repeated runs and thread counts.
- `fd --sort path`, `--sort size`, `--sort modified`, `--sort accessed`, `--sort created`, and `--sort extension` each apply the documented ordering rule.
- `--sort size` orders by numeric byte length (e.g. a 9-byte file precedes a 100-byte file), not lexicographically.
- `--sort none` (and omission of `--sort`) preserves the existing streaming output and buffering behavior with no observable change.
- `--reverse` reverses the sorted output; using `--reverse` without a non-`none` `--sort` key produces a non-zero clap usage error.
- `--sort <key> --max-results N` truncates AFTER sorting (returns the N smallest/first entries in sort order).
- `--sort <key> --exec CMD` and `--exec-batch CMD` invoke commands in sorted order.
- Entries missing metadata or a timestamp are ordered by the documented fallback and never cause a panic.
- An invalid `--sort` value exits with a usage error that lists the valid keys.
- All pre-existing tests in `tests/tests.rs` and module unit tests continue to pass unchanged.
