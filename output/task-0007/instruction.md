Gin can stream an arbitrary payload to a client with `Context.DataFromReader`, but it always sends the whole body with a 200 status and ignores the client's `Range` request header. Only `Context.File` benefits from range handling today, because it delegates to the standard library's `http.ServeContent`. Applications that stream generated artifacts, blobs pulled from object storage, media, or embedded-FS assets through `DataFromReader`/`FileFromFS` therefore cannot support resumable downloads, media seeking, or byte-range fetches at all.

We want first-class, RFC 7233 compliant partial-content support that is reusable across the `render`, root `gin`, and `internal/fs` packages.

Required behavior:

1. A new `render` primitive that parses a `Range` header against a known content size and renders either a single `206 Partial Content` response (with a `Content-Range` header) or a `multipart/byteranges` response when multiple satisfiable ranges are requested.
2. A new `Context.DataFromReaderRange` entry point that seeks an `io.ReadSeeker`, honors the request `Range` header, and sets `Accept-Ranges: bytes` on every response it produces. With no `Range` header it must behave exactly like `DataFromReader` (status `code`, `Content-Length`, `extraHeaders`, full body). When the range is syntactically valid but unsatisfiable it must emit `416 Range Not Satisfiable` with `Content-Range: bytes */<size>` and an empty body.
3. `If-Range` conditional handling: when the request carries `If-Range`, the range is honored only if the supplied validator (an ETag in `extraHeaders["ETa...

Public API contract:
- package render: type Range struct { Start int64; Length int64 }
- package render: func ParseRange(rangeHeader string, size int64) ([]Range, error)
- package render: func FormatContentRange(start, length, size int64) string  // returns "bytes <start>-<start+length-1>/<size>"
- package render: var ErrNoOverlap = errors.New("invalid range: failed to overlap")
- package render: type ReaderRange struct { ContentType string; ContentLength int64; ReadSeeker io.ReadSeeker; Ranges []Range; Boundary string; Headers map[string]string }
- package render: func (r ReaderRange) Render(w http.ResponseWriter) error
- package render: func (r ReaderRange) WriteContentType(w http.ResponseWriter)
- package gin: func (c *Context) DataFromReaderRange(code int, contentLength int64, contentType string, reader io.ReadSeeker, extraHeaders map[string]string) bool
- package gin: func (c *Context) FileFromFSRange(filepath string, filesystem fs.FS)
- package gin: Engine struct exported field `MaxRanges int` (0 means use the internal default of 16 ranges)
- Observable HTTP contract: responses that engage range logic always carry `Accept-Ranges: bytes`; single-range -> 206 + `Content-Range`; multi-range -> 206 + `multipart/byteranges`; unsatisfiable -> 416 + `Content-Range: bytes */<size>`; no Range / rejected If-Range / over-limit -> 200 full body.

Acceptance criteria:
- render.ParseRange parses a `bytes=` header against a size, supporting `start-end`, `start-`, and `-suffix` forms, clamping `end` to `size-1`, skipping fully out-of-window parts, and returning render.ErrNoOverlap when no part overlaps.
- render.ParseRange returns an error for a missing/foreign unit prefix, malformed numbers, or inverted ranges (start > end).
- render.ReaderRange with exactly one Range renders status 206, a `Content-Range: bytes <start>-<end>/<size>` header, `Content-Length` equal to the range length, and exactly the requested bytes as body.
- render.ReaderRange with two or more Ranges renders status 206 with `Content-Type: multipart/byteranges; boundary=<boundary>` and one MIME part per range carrying the original Content-Type plus a per-part `Content-Range` header.
- Context.DataFromReaderRange with no `Range` request header produces identical output to Context.DataFromReader (same status code, Content-Type, Content-Length, extra headers, full body) and additionally sets `Accept-Ranges: bytes`.
- Context.DataFromReaderRange with a satisfiable single range returns 206 and the correct byte slice; the method returns true only when a partial (206) response was sent.
- Context.DataFromReaderRange with a syntactically valid but unsatisfiable range (start >= size) returns 416, sets `Content-Range: bytes */<size>`, writes no body, and returns false.
- When `If-Range` is present and does not match the supplied ETag or Last-Modified validator, the full body is returned with status 200 and the `Range` header is ignored.
- When the number of requested ranges exceeds the effective Engine.MaxRanges limit, the full 200 body is served and no 206/multipart response is produced.
- Context.FileFromFSRange serves a file from an io/fs.FS honoring the `Range` header (206/multipart/416 as appropriate) and returns 404 for a missing path.
- All pre-existing DataFromReader, File, FileFromFS, and render.Reader outputs are unchanged; the full test suite passes under both default and `nomsgpack` tags.
