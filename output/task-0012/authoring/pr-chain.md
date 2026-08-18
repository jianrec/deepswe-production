# PR Chain

## 1. accept-engine

Depends on: []

Modules: internal/accept

Files: internal/accept/accept.go

Behavior: Introduce the shared media-type engine: MediaRange type, ParseAccept, MediaRange.Matches, and Negotiate. Implement quality parsing, wildcard specificity ordering, q=0 rejection, and tolerant handling of malformed segments using mime.ParseMediaType for parameter splitting.

## 2. binding-content-type

Depends on: ['accept-engine']

Modules: binding

Files: binding/binding.go, binding/binding_nomsgpack.go

Behavior: Make Default parameter-tolerant by stripping media-type parameters before the content-type switch, and add Preferred(contentType) returning the matching Binding (nil if unknown). Mirror the change across both build-tag variants so behavior is identical with and without nomsgpack.

## 3. render-registry

Depends on: ['accept-engine']

Modules: render

Files: render/negotiate.go, render/render.go

Behavior: Add RenderFactory, Offer, Negotiator (NewNegotiator/Offers/Match) and DefaultOffers built on the render.Render interface. Match uses internal/accept.Negotiate against registered media types and returns the corresponding Offer.

## 4. context-negotiation

Depends on: ['binding-content-type', 'render-registry']

Modules: gin

Files: negotiate.go, context.go, gin.go

Behavior: Add Context.NegotiateRender and ErrNotAcceptable in negotiate.go, upgrade Context.NegotiateFormat in context.go to use the shared engine, and add the Engine.Negotiator field plus its default wiring in gin.go. Nil-negotiator fallback resolves to Engine.Negotiator then render.DefaultOffers().
