Hermes tracks token usage and translates it to money through `agent/usage_pricing.py` and `agent/credits_tracker.py`, and it renders spend in `agent/billing_view.py`, `agent/display.py`, and the gateway footer. Today there is no way for an operator to cap how much a session (or a calendar day) is allowed to spend, and no structured signal when spend crosses a threshold. Users running the agent unattended on a VPS or from a chat platform want a budget they can set once and have the agent honor.

Add a spend-budget subsystem. Operators configure one or more USD budgets scoped to `session`, `daily`, or `total`. As turns complete, per-turn USD cost (derived from the existing usage/pricing path) is accumulated per scope. When accumulated spend crosses a warn threshold (default 80% of the limit) the subsystem emits a one-shot warn event; when it reaches or exceeds the limit it emits an exceeded event. The `daily` scope resets at the local midnight boundary using an injectable clock so behavior is deterministic under test.

Budgets are surfaced three ways: (1) a compact status line in the billing view and the gateway runtime footer showing spent/limit and remaining; (2) a structured monit...

Public API contract:
- agent/spend_budget.py: @dataclass(frozen=True) class BudgetLimit: scope: str; limit_usd: float; warn_ratio: float = 0.8
- agent/spend_budget.py: @dataclass(frozen=True) class BudgetStatus: scope: str; spent_usd: float; limit_usd: float; ratio: float; remaining_usd: float; warned: bool; exceeded: bool
- agent/spend_budget.py: @dataclass(frozen=True) class BudgetEvent: kind: str; scope: str; spent_usd: float; limit_usd: float; ratio: float; ts: float  # kind in {'warn','exceeded','reset'}
- agent/spend_budget.py: class SpendBudgetTracker: def __init__(self, limits: list[BudgetLimit], *, clock: Callable[[], float] | None = None) -> None
- agent/spend_budget.py: SpendBudgetTracker.record_usage(self, *, scope: str, cost_usd: float, session_id: str | None = None) -> list[BudgetEvent]
- agent/spend_budget.py: SpendBudgetTracker.snapshot(self) -> dict[str, BudgetStatus]
- agent/spend_budget.py: SpendBudgetTracker.remaining(self, scope: str) -> float | None
- agent/spend_budget.py: SpendBudgetTracker.is_exceeded(self, scope: str) -> bool
- agent/spend_budget.py: SpendBudgetTracker.reset(self, scope: str) -> None
- agent/spend_budget.py: def parse_budget_config(raw: dict[str, Any] | None) -> list[BudgetLimit]
- agent/spend_budget.py: def format_budget_status(status: BudgetStatus, *, use_color: bool = True) -> str
- agent/usage_pricing.py: def turn_cost_usd(usage: dict[str, Any] | Any, pricing: dict[str, Any] | Any) -> float  # additive; returns 0.0 when pricing unknown
- agent/monitoring/events.py: BUDGET_THRESHOLD_EVENT: str = 'budget.threshold'  # payload dict keys: scope, kind, spent_usd, limit_usd, ratio, session_id, ts
- Config keys: budget.enabled (bool, default false), budget.session_limit_usd (float|null, default null), budget.daily_limit_usd (float|null, default null), budget.total_limit_usd (float|null, default null), budget.warn_ratio (float, default 0.8), budget.enforce (bool, default false)
- CLI: `hermes config set budget.session_limit_usd <float>` and sibling keys; run/oneshot flag `--budget-limit <USD>` sets the session-scope limit for one invocation

Acceptance criteria:
- A new module `agent/spend_budget.py` exposes `SpendBudgetTracker`, `BudgetLimit`, `BudgetEvent`, `BudgetStatus`, `parse_budget_config`, and `format_budget_status` with the exact signatures in the public API contract.
- `parse_budget_config` accepts the `budget.*` config mapping and returns a list of `BudgetLimit`; unknown/malformed input yields an empty list (disabled) without raising.
- `SpendBudgetTracker.record_usage(scope=..., cost_usd=...)` accumulates spend per scope and returns a list of `BudgetEvent`; a warn event fires at most once per scope until reset, and an exceeded event fires at most once per scope until reset.
- The `daily` scope resets accumulated spend when the injected clock crosses local midnight; `session` and `total` never auto-reset.
- `SpendBudgetTracker.snapshot()` returns `{scope: BudgetStatus}` and `remaining(scope)`/`is_exceeded(scope)` reflect current accumulation; `remaining` returns None for an unconfigured scope.
- Per-turn USD cost is computed via a new additive helper in `agent/usage_pricing.py` and wired through `agent/credits_tracker.py` so budgets update as turns finalize.
- A `budget.threshold` monitoring event is emitted through the existing emitter with payload keys `scope`, `kind`, `spent_usd`, `limit_usd`, `ratio`, `session_id`, `ts`; the payload survives redaction with amounts intact and no secrets present.
- `agent/billing_view.py` and `gateway/runtime_footer.py` render a budget status line only when budgets are enabled; when disabled their output is byte-for-byte unchanged.
- Config keys `budget.enabled`, `budget.session_limit_usd`, `budget.daily_limit_usd`, `budget.total_limit_usd`, `budget.warn_ratio`, and `budget.enforce` are defined with defaults in `hermes_cli/config_defaults.py` and settable via `hermes config set`.
- With `budget.enabled=false`, all four preflight test suites pass unmodified and no budget code path executes.
- Malformed or negative limits are ignored (that scope is treated as unconfigured) and a warning is logged; the process never crashes on bad budget config.
