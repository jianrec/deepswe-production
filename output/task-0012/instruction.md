Gin can render many response formats (JSON, XML, YAML, TOML, plain text) and select a request binder from the Content-Type header, but both selection paths are naive. `binding.Default(method, contentType)` switches on the *raw* Content-Type string, so a perfectly valid request header such as `application/json; charset=utf-8` fails to match `MIMEJSON` and silently falls back to form binding. On the response side there is no principled way to pick a renderer from the client's `Accept` header: `Context.NegotiateFormat` does not honor quality (`q=`) values, does not understand media-range wildcards (`*/*`, `application/*`), and has no shared, tested notion of media-range specificity. Applications that want true content negotiation must reimplement Accept parsing by hand.

This task introduces a small, shared media-type engine and wires it through three subsystems so request and response format selection are consistent and standards-compliant.

Required behavior:
- A new `internal/accept` package parses `Accept` headers into ordered media ranges (type, subtype, quality, params) and negotiates a best offer from a caller-supplied list. Ranking is by descending quality, then by descending specificity (a fully specified type beats `type/*`, which beats `*/*`), then by the caller's offer order. A media range with `q=0` explicitly rejects a match. Malformed segments are skipped, never panicking.
- `binding.Default` and a new `binding.Preferred(contentType string) Binding` strip media-type parameters before matching, so `application/json; charset=utf-8` binds as JSON. All existing exact-match behavior (including `GET`→Form and unknown→Form) must be preserved.
- A new `render.Negotiator` maps media types to renderer factories and exposes `Match(accept string) (Offer, bool)`. `render.DefaultOffers()` supplies JSON/XML/YAML/TOML/text offers.
- `Context.NegotiateRender(code int, data any, n *render.Negotiator) bool` selects a renderer for the request's `Accept` header, writes the response, an...

Public API contract:
- package internal/accept: type MediaRange struct { Type string; Subtype string; Quality float64; Params map[string]string }
- package internal/accept: func ParseAccept(header string) []MediaRange
- package internal/accept: func (m MediaRange) Matches(mediaType string) bool
- package internal/accept: func Negotiate(header string, offers []string) string
- package binding: func Default(method, contentType string) Binding (existing symbol; now parameter-tolerant)
- package binding: func Preferred(contentType string) Binding (new; returns nil for unknown types)
- package render: type RenderFactory func(data any) Render
- package render: type Offer struct { MediaType string; Factory RenderFactory }
- package render: type Negotiator struct { /* unexported fields */ }
- package render: func NewNegotiator(offers ...Offer) *Negotiator
- package render: func (n *Negotiator) Offers() []Offer
- package render: func (n *Negotiator) Match(accept string) (Offer, bool)
- package render: func DefaultOffers() []Offer
- package gin: func (c *Context) NegotiateRender(code int, data any, n *render.Negotiator) bool
- package gin: func (c *Context) NegotiateFormat(offered ...string) string (existing symbol; behavior upgraded to honor q-values and wildcards)
- package gin: field Engine.Negotiator *render.Negotiator (new; default negotiator used when NegotiateRender receives nil)
- package gin: var ErrNotAcceptable = errors.New("gin: no acceptable content type offered")

Acceptance criteria:
- internal/accept.ParseAccept parses quality values, wildcards, and parameters, returning media ranges sorted by (quality desc, specificity desc, input order); malformed entries are skipped without panic.
- internal/accept.Negotiate(header, offers) returns the best matching offer honoring q-values and wildcards, returns "" when the header is present but nothing is acceptable, and returns the first offer when the header is empty or "*/*".
- A media range with q=0 never matches its media type even when a wildcard would otherwise apply.
- binding.Default and binding.Preferred match content types that carry parameters (e.g. 'application/json; charset=utf-8' -> JSON), while preserving all previous exact-match results including GET->Form and unknown->Form.
- render.NewNegotiator / render.DefaultOffers build a registry whose Match returns the correct Offer for a given Accept header and false when unacceptable.
- Context.NegotiateRender writes the negotiated representation with the requested status code and returns true on success.
- Context.NegotiateRender writes HTTP 406 and returns false when no offered media type is acceptable, and falls back to the engine default then render.DefaultOffers() when passed a nil Negotiator.
- Context.NegotiateFormat honors q-values and wildcards and returns "" when the Accept header excludes every offered format.
- The package compiles and all targeted tests pass under both the default build and the 'nomsgpack' build tag.
